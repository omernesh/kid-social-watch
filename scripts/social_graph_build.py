#!/usr/bin/env python3
"""social_graph_build.py — build a weighted social graph for a child from chatlytics hub data.

Companion for the kid-social-watch skill (monthly on the 1st, or ad hoc in a
deep-dive) and feeder for social_graph_leiden.py (the algorithm runner). Pulls the
last N days of the child's DM + group activity from the hub and emits a graph spec.

Weights (recipe verified 2026-09-03 on a live case):
  - DM pair (child <-> contact): 3 * log1p(messages in the DM window)
  - Shared-group co-activity: log1p(min(count_a, count_b)) per group (per group,
    members capped to the top --top-per-group senders)

Nodes merge across @lid / @c.us forms by phone when resolvable
(cache: <state_dir>/social_graph_idmap.json).

Usage:
  python3 social_graph_build.py <child_key> [--days 30] [--run]
  # config: the KID_WATCH_CONFIG file (same schema as kid_replies_marker.py);
  # optional "leiden_python" = interpreter with leidenalg+igraph (default: current)

Output: <state_dir>/social_graph_<child>.json (+ a dated copy) and a compact
summary on stdout — with the Leiden runner's full output when --run succeeds.
"""
import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_CONFIG = os.path.expanduser("~/.hermes/kid-social-watch.json")


class Ctx:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hub = (cfg.get("hub_base") or "").rstrip("/")
        self.key = open(os.path.expanduser(cfg["key_file"])).read().strip()
        self.state_dir = os.path.expanduser(cfg.get("state_dir") or "~/.hermes/data")

    def hub_get(self, path, timeout=30):
        req = urllib.request.Request(self.hub + path, headers={
            "Authorization": "Bearer " + self.key,
            "User-Agent": "curl/8.12.1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def hub_action(self, action, params, session, timeout=30):
        body = json.dumps({"action": action, "params": params, "session": session}).encode()
        req = urllib.request.Request(self.hub + "/api/v1/actions", data=body, headers={
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json",
            "User-Agent": "curl/8.12.1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = json.loads(r.read().decode("utf-8"))
        try:
            return json.loads(raw["result"]["content"][0]["text"])
        except Exception:
            return raw

    def load_cache(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def save_cache(self, path, d):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)


def sender_of(m):
    s = m.get("participant")
    if s:
        return s
    parts = (m.get("id") or "").split("_")
    if len(parts) >= 4 and "@" in parts[-1]:
        return parts[-1]
    return None


def main():
    ap = argparse.ArgumentParser(description="Build a weighted social-graph spec for a child")
    ap.add_argument("child")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--from", dest="from_date", default=None, help="window start date YYYY-MM-DD (overrides --days)")
    ap.add_argument("--to", dest="to_date", default=None, help="window end date YYYY-MM-DD, inclusive (default: now)")
    ap.add_argument("--min-count", type=int, default=3, help="min messages to become a node")
    ap.add_argument("--max-nodes", type=int, default=40)
    ap.add_argument("--top-per-group", type=int, default=15)
    ap.add_argument("--limit", type=int, default=1200, help="messages pulled per chat (newest-first)")
    ap.add_argument("--run", action="store_true", help="also run social_graph_leiden.py")
    ap.add_argument("--json", action="store_true", help="print the spec JSON instead of the summary")
    ap.add_argument("--config", default=os.environ.get("KID_WATCH_CONFIG") or DEFAULT_CONFIG)
    args = ap.parse_args()

    try:
        cfg = json.load(open(os.path.expanduser(args.config)))
    except Exception as e:
        print("config error: %s" % e)
        return 2
    children = cfg.get("children") or {}
    if args.child not in children:
        print("unknown child %r — configured: %s" % (args.child, ", ".join(sorted(children))))
        return 2
    ccfg = children[args.child]
    session = ccfg["session"]
    child_label = ccfg.get("name") or args.child
    ctx = Ctx(cfg)
    now = int(time.time())
    import datetime as _dt
    il = _dt.timezone(_dt.timedelta(hours=3))
    if getattr(args, "from_date", None):
        ws = int(_dt.datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=il).timestamp())
    else:
        ws = now - args.days * 86400
    we = now
    if getattr(args, "to_date", None):
        we = int((_dt.datetime.strptime(args.to_date, "%Y-%m-%d") + _dt.timedelta(days=1)).replace(tzinfo=il).timestamp()) - 1

    try:
        chats = ctx.hub_action("getChats", {}, session)
    except Exception as e:
        print("getChats failed: %s" % e)
        return 1
    if isinstance(chats, dict):
        chats = chats.get("chats") or chats.get("data") or []
    active = [c for c in chats if (c.get("conversationTimestamp") or 0) >= ws]
    active.sort(key=lambda c: c.get("conversationTimestamp") or 0, reverse=True)

    dm_total, dm_label = {}, {}
    grp_counts = {}
    names = {}
    for ch in active:
        cid = ch.get("id") or ""
        is_group = cid.endswith("@g.us")
        is_dm = cid.endswith(("@c.us", "@lid", "@s.whatsapp.net"))
        if not (is_group or is_dm):
            continue
        try:
            rows = ctx.hub_get("/api/v1/messages?chatId=%s&session=%s&limit=%d" % (
                urllib.parse.quote(cid, safe=""), session, args.limit))
        except Exception:
            continue
        if isinstance(rows, dict):
            rows = rows.get("messages") or rows.get("data") or []
        for m in rows:
            mts = m.get("timestamp") or 0
            if mts < ws or mts > we:
                continue
            nm = (m.get("_data") or {}).get("pushName")
            if is_dm:
                dm_total[cid] = dm_total.get(cid, 0) + 1
                if nm and not m.get("fromMe"):
                    dm_label.setdefault(cid, nm)
            else:
                snd = "__child__" if m.get("fromMe") else sender_of(m)
                if not snd:
                    continue
                grp_counts.setdefault(cid, {})
                grp_counts[cid][snd] = grp_counts[cid].get(snd, 0) + 1
                if nm:
                    names.setdefault(snd, nm)
        if is_dm and cid not in dm_label and ch.get("name"):
            dm_label[cid] = ch["name"]

    # --- merge @lid/@c.us forms by phone (cached)
    idmap_path = os.path.join(ctx.state_dir, "social_graph_idmap.json")
    idmap = ctx.load_cache(idmap_path)

    def canon(sid):
        if sid == "__child__":
            return "__child__"
        if sid in idmap:
            return idmap[sid]
        key = None
        digits = re.sub(r"\D", "", sid.split("@")[0])
        if sid.endswith("@c.us") and digits:
            key = "pn:" + digits[-9:]
        elif sid.endswith("@lid"):
            try:
                p = ctx.hub_action("findPhoneByLid", {"lid": sid}, session)
                pn = (p or {}).get("pn") or ""
                pn_digits = re.sub(r"\D", "", pn)
                if pn_digits:
                    key = "pn:" + pn_digits[-9:]
            except Exception:
                pass
        if not key:
            key = "id:" + sid
        idmap[sid] = key
        return key

    node_counts, node_label = {}, {}
    dm_pairs = []
    for cid, total in dm_total.items():
        if total <= 0:
            continue
        ck = canon(cid)
        node_counts[ck] = node_counts.get(ck, 0) + total
        if cid in dm_label:
            node_label.setdefault(ck, dm_label[cid])
        dm_pairs.append((ck, total))
    grp_tops = {}
    for cid, counts in grp_counts.items():
        ccanon = {}
        for snd, c in counts.items():
            ck = canon(snd)
            ccanon[ck] = ccanon.get(ck, 0) + c
            if snd in names:
                node_label.setdefault(ck, names[snd])
        grp_tops[cid] = ccanon
        for ck, c in ccanon.items():
            node_counts[ck] = node_counts.get(ck, 0) + c
    node_label["__child__"] = child_label

    ranked = sorted(((ck, c) for ck, c in node_counts.items() if ck != "__child__"),
                    key=lambda kv: -kv[1])
    keep = {"__child__"}
    for ck, c in ranked:
        if c >= args.min_count and len(keep) < args.max_nodes:
            keep.add(ck)

    labels, used = {}, set()
    _nm = {}
    try:
        _nm = json.load(open("/home/omer/kidreports/data/name-map.json"))
    except Exception:
        _nm = {}
    _by_jid = _nm.get("by_jid") or {}
    _canon = _nm.get("canonical") or {}

    def nm_resolve(ck):
        """Resolve an unnamed canon key -> display name via the name-map (or phone format)."""
        def dig(s):
            return re.sub(r"\D", "", s)
        if ck.startswith("pn:"):
            tail = ck[3:]
            for jid, name in _by_jid.items():
                if dig(jid).endswith(tail):
                    return _canon.get((name or "").lower(), name)
            if len(tail) == 9:
                return "0%s-%s-%s" % (tail[:2], tail[2:5], tail[5:])
            return None
        if ck.startswith("id:"):
            sid = ck[3:]
            for key in (sid, sid.split("@")[0]):
                if key and key in _by_jid:
                    name = _by_jid[key]
                    return _canon.get((name or "").lower(), name)
            tail = dig(sid.split("@")[0])[-9:]
            if len(tail) == 9:
                for jid, name in _by_jid.items():
                    if dig(jid).endswith(tail):
                        return _canon.get((name or "").lower(), name)
                return "0%s-%s-%s" % (tail[:2], tail[2:5], tail[5:])
        return None

    for ck in keep:
        base = node_label.get(ck) or nm_resolve(ck) or ("…" + ck[-6:])
        lbl, i = base, 2
        while lbl in used:
            lbl = "%s #%d" % (base, i)
            i += 1
        used.add(lbl)
        labels[ck] = lbl

    edges = {}

    def add_edge(a, b, w):
        if w <= 0:
            return
        key = (a, b) if a <= b else (b, a)
        edges[key] = edges.get(key, 0) + w

    for ck, total in dm_pairs:
        if ck in keep and ck != "__child__":
            add_edge(labels["__child__"], labels[ck], 3 * math.log1p(total))
    for cid, ccanon in grp_tops.items():
        top = sorted(((ck, c) for ck, c in ccanon.items() if ck in keep),
                     key=lambda kv: -kv[1])[: args.top_per_group]
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                a, ca = top[i]
                b, cb = top[j]
                add_edge(labels[a], labels[b], math.log1p(min(ca, cb)))

    spec = {"target": labels["__child__"],
            "nodes": sorted(labels[ck] for ck in keep),
            "edges": [[a, b, round(w, 4)] for (a, b), w in sorted(edges.items())]}
    ctx.save_cache(idmap_path, idmap)
    spec_path = os.path.join(ctx.state_dir, "social_graph_%s.json" % args.child)
    ctx.save_cache(spec_path, spec)
    ctx.save_cache(spec_path.replace(".json", "_%s.json" % time.strftime("%Y%m")), spec)

    if args.json:
        print(json.dumps(spec, ensure_ascii=False, indent=1))
        return 0

    print("🕸️ %s: %d nodes / %d edges (last %dd%s)" % (
        child_label, len(keep), len(spec["edges"]), args.days,
        "" if not args.run else ""))

    if args.run:
        py = os.path.expanduser(cfg.get("leiden_python") or sys.executable)
        runner = os.path.join(os.path.dirname(os.path.realpath(__file__)), "social_graph_leiden.py")
        try:
            r = subprocess.run([py, runner, spec_path, "--target", spec["target"]],
                               capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                print(r.stdout.strip())
            else:
                print("leiden runner failed (rc=%d): %s" % (
                    r.returncode, (r.stderr or "").strip().splitlines()[-1] if r.stderr else "?"))
        except Exception as e:
            print("leiden runner error: %s" % e)
    else:
        print("(spec saved — add --run to execute the Leiden solver)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
