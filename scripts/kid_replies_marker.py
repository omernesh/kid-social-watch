#!/usr/bin/env python3
"""kid_replies_marker.py — direct (quoted) replies to a child's WhatsApp messages.

Marker for the daily kid social-watch reports: who replied DIRECTLY (quoted
reply) to messages the child sent, so "their messages aren't ignored" becomes
measurable. Deterministic, hub-side, ~10s per run.

Data source: a chatlytics hub (WAHA-native ids + replyTo with quoted author LID).
Detection: a row is a direct reply to the child when
  replyTo.id ∈ child's sent-message stanzas  OR  replyTo.participant ∈ child's LID forms.
Classification:
  - "msg"    — quoted stanza = one of the child's sent messages in the scan window
  - "status" — the same quoted stanza appears in ≥2 different chats (a status/broadcast
               message can only be quoted across chats; per-chat sends get unique stanzas)
  - "old"    — quoted an earlier message (not in the window)

Config (default ~/.hermes/kid-social-watch.json, override with --config or
KID_WATCH_CONFIG). No secrets in the config either — the hub key is read at
runtime from the configured key file:

  {
    "hub_base": "https://your-hub.example",
    "key_file": "~/.hermes/chatlytics-key.txt",
    "state_dir": "~/.hermes/data",
    "lang": "en",
    "children": {
      "alex": {"session": "alex_line", "phone": "972500000000", "name": "Alex"}
    }
  }

Usage:
  kid_replies_marker.py <child_key>                 # window = since local midnight
  kid_replies_marker.py <child_key> --hours 32      # window = last N hours
  kid_replies_marker.py <child_key> --json
"""
import json, sys, time, argparse, datetime, os, re
import urllib.request, urllib.parse

DEFAULT_CONFIG = os.path.expanduser("~/.hermes/kid-social-watch.json")

STAN_RE = re.compile(r"[A-F0-9]{12,}")

LANGS = {
    "en": {
        "no_data": "↩️ Direct replies: no data (fetch error)",
        "none": "↩️ Direct replies to {name}: none (out of {sent} outgoing messages in window)",
        "header": "↩️ Direct replies (quotes) — {n} received: {bits} | {sent} outgoing messages in the window",
        "bit_msg": "{n} to messages ({u} distinct quoted)",
        "bit_status": "{n} to statuses",
        "bit_old": "{n} to earlier messages",
        "examples": "• Examples: ",
        "tag_status": "(status)",
        "media": "[media]",
    },
    "he": {
        "no_data": "↩️ מענה ישיר: אין נתונים (שגיאה טכנית בשליפה)",
        "none": "↩️ מענה ישיר להודעות של {name}: אין תגובות ישירות (מתוך {sent} הודעות יוצאות בחלון)",
        "header": "↩️ מענה ישיר (ציטוטים) — {n} תגובות שהתקבלו: {bits} | סה\"כ {sent} הודעות יוצאות בחלון",
        "bit_msg": "{n} על הודעות ({u} מהודעותיה בחלון)",
        "bit_status": "{n} על סטטוסים",
        "bit_old": "{n} על הודעות מוקדמות",
        "examples": "• דוגמאות: ",
        "tag_status": "(סטטוס)",
        "media": "[מדיה]",
    },
}


def load_config(path):
    with open(path) as f:
        return json.load(f)


class Ctx:
    def __init__(self, cfg, config_path):
        self.cfg = cfg
        self.config_path = config_path
        self.hub = (cfg.get("hub_base") or "").rstrip("/")
        self.key = open(os.path.expanduser(cfg["key_file"])).read().strip()
        self.state_dir = os.path.expanduser(cfg.get("state_dir") or "~/.hermes/data")
        self.lid_cache = os.path.join(self.state_dir, "kid_lids.json")
        self.name_cache = os.path.join(self.state_dir, "kid_replier_names.json")
        self.last_json = os.path.join(self.state_dir, "kid_replies_last.json")
        self.lang = LANGS.get(cfg.get("lang") or "en", LANGS["en"])

    def hub_get(self, path, timeout=25):
        req = urllib.request.Request(self.hub + path, headers={
            "Authorization": "Bearer " + self.key,
            "User-Agent": "curl/8.12.1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def hub_action(self, action, params, session, timeout=25):
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

    def _load_cache(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cache(self, path, d):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)

    def load_lids(self):
        return self._load_cache(self.lid_cache)

    def save_lids(self, d):
        self._save_cache(self.lid_cache, d)

    def load_names(self):
        return self._load_cache(self.name_cache)

    def save_names(self, d):
        self._save_cache(self.name_cache, d)

    def resolve_name(self, lid, session, cache):
        """lid -> phone -> contact name (cached)."""
        if lid in cache:
            return cache[lid]
        name = None
        try:
            p = self.hub_action("findPhoneByLid", {"lid": lid}, session)
            pn = (p or {}).get("pn")
            if pn:
                c = self.hub_action("getContact", {"contactId": pn.split("@")[0]}, session)
                if not (c or {}).get("name"):
                    c = self.hub_action("getContact", {"contactId": pn}, session)
                name = (c or {}).get("name") or (c or {}).get("pushName")
        except Exception:
            pass
        if name:
            cache[lid] = name
            self.save_names(cache)
        return name


def stanza_of(mid):
    mids = STAN_RE.findall(mid or "")
    return mids[-1] if mids else None


def sender_of(m):
    s = m.get("participant")
    if s:
        return s
    parts = (m.get("id") or "").split("_")
    if len(parts) >= 4 and "@" in parts[-1]:
        return parts[-1]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("child", help="child key as defined in the config (children map)")
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--from", dest="from_date", default=None, help="window start date YYYY-MM-DD (overrides --hours)")
    ap.add_argument("--to", dest="to_date", default=None, help="window end date YYYY-MM-DD, inclusive (default: now)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", default=os.environ.get("KID_WATCH_CONFIG") or DEFAULT_CONFIG)
    ap.add_argument("--limit-per-chat", type=int, default=150)
    ap.add_argument("--max-chats", type=int, default=70)
    args = ap.parse_args()

    try:
        cfg = load_config(os.path.expanduser(args.config))
    except Exception as e:
        print("config error: %s (expected at %s — see the docstring for the schema)" % (e, args.config))
        return 2
    children = cfg.get("children") or {}
    if args.child not in children:
        print("unknown child %r — configured keys: %s" % (args.child, ", ".join(sorted(children)) or "(none)"))
        return 2
    ccfg = children[args.child]

    ctx = Ctx(cfg, args.config)
    L = ctx.lang
    session = ccfg["session"]
    phone = ccfg.get("phone")

    now = int(time.time())
    il = datetime.timezone(datetime.timedelta(hours=3))
    if args.from_date:
        ws = int(datetime.datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=il).timestamp())
    elif args.hours:
        ws = now - int(args.hours * 3600)
    else:
        ws = int(datetime.datetime.fromtimestamp(now, il).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
    we = now
    if args.to_date:
        we = int((datetime.datetime.strptime(args.to_date, "%Y-%m-%d") + datetime.timedelta(days=1)).replace(tzinfo=il).timestamp()) - 1

    result = {"child": args.child, "session": session, "window_start": ws, "window_end": we,
              "sent": 0, "replies": [], "chats_scanned": 0, "errors": []}
    try:
        chats = ctx.hub_action("getChats", {}, session)
    except Exception as e:
        result["errors"].append("getChats: %s" % e)
        return finish(ctx, result, args, ccfg)

    if isinstance(chats, dict):
        chats = chats.get("chats") or chats.get("data") or []
    active = [c for c in chats if (c.get("conversationTimestamp") or 0) >= ws]
    active.sort(key=lambda c: c.get("conversationTimestamp") or 0, reverse=True)
    active = active[: args.max_chats]

    lids = ctx.load_lids()
    child_lid = lids.get(args.child)
    lid_candidates = {}

    # direct discovery: phone -> LID via the hub's lookup table
    if not child_lid and phone:
        try:
            _d = ctx.hub_action("findLidByPhone", {"phone": phone}, session)
            child_lid = (_d or {}).get("lid")
            if child_lid:
                lids[args.child] = child_lid
                ctx.save_lids(lids)
        except Exception:
            pass

    stamps = {}
    replies = []
    for ch in active:
        cid = ch.get("id")
        try:
            rows = ctx.hub_get("/api/v1/messages?chatId=%s&session=%s&limit=%d" % (
                urllib.parse.quote(cid, safe=""), session, args.limit_per_chat))
        except Exception as e:
            result["errors"].append("%s: %s" % (cid, e))
            continue
        result["chats_scanned"] += 1
        if isinstance(rows, dict):
            rows = rows.get("messages") or rows.get("data") or []
        for m in rows:
            ts = m.get("timestamp") or 0
            if ts < ws or ts > we:
                continue
            st = stanza_of(m.get("id"))
            if m.get("fromMe"):
                if st:
                    stamps[st] = {"ts": ts, "body": m.get("body"), "chat": cid,
                                  "hasMedia": m.get("hasMedia")}
                result["sent"] += 1
                continue
            rt = m.get("replyTo") or {}
            if not rt:
                continue
            part = rt.get("participant")
            if part:
                lid_candidates[part] = lid_candidates.get(part, 0) + 1
            if (rt.get("id") in stamps) or (child_lid and part == child_lid):
                d = m.get("_data") or {}
                snd = sender_of(m)
                replies.append({
                    "ts": ts,
                    "replier": d.get("pushName") or snd or "?",
                    "sender": snd,
                    "body": m.get("body"),
                    "quoted_body": rt.get("body"),
                    "quoted_stanza": rt.get("id"),
                    "chat": cid,
                })

    # classify + attach the child's own message body
    per_st_chats = {}
    for r in replies:
        st = r.get("quoted_stanza")
        if st:
            per_st_chats.setdefault(st, set()).add(r["chat"])
    for r in replies:
        st = r.get("quoted_stanza")
        own = stamps.get(st)
        if st and own is not None:
            r["class"] = "msg"
        elif st and len(per_st_chats.get(st, ())) >= 2:
            r["class"] = "status"
        else:
            r["class"] = "old"
        r["own_body"] = own.get("body") if own else None
        r["own_has_media"] = own.get("hasMedia") if own else None

    # name resolution for senders without a pushName (cached across runs)
    names = ctx.load_names()
    resolved = 0
    for r in replies:
        snd = r.get("sender")
        if snd and r["replier"] == snd and resolved < 6:
            nm = ctx.resolve_name(snd, session, names)
            r["replier"] = nm or ("…" + snd.split("@")[0][-6:])
            resolved += 1

    result["replies"] = sorted(replies, key=lambda x: x["ts"])
    result["unique_quoted"] = len({r["quoted_stanza"] for r in replies if r["class"] == "msg"})
    result["status_replies"] = sum(1 for r in replies if r["class"] == "status")
    result["old_replies"] = sum(1 for r in replies if r["class"] == "old")

    # lid discovery for children without a cached lid
    if not child_lid and lid_candidates:
        ranked = sorted(lid_candidates.items(), key=lambda kv: -kv[1])[:25]
        for lid, _n in ranked:
            try:
                p = ctx.hub_action("findPhoneByLid", {"lid": lid}, session)
                pn = (p or {}).get("pn") or ""
                if pn.split("@")[0] == phone:
                    child_lid = lid
                    lids[args.child] = lid
                    ctx.save_lids(lids)
                    break
            except Exception:
                continue

    return finish(ctx, result, args, ccfg, child_lid)


def finish(ctx, result, args, ccfg=None, child_lid=None):
    L = ctx.lang
    os.makedirs(os.path.dirname(ctx.last_json), exist_ok=True)
    with open(ctx.last_json, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    replies = result["replies"]
    sent = result["sent"]
    name = (ccfg or {}).get("name") or result["child"]

    def short(s, n=40):
        s = (s or "").strip().replace("\n", " ")
        return (s[: n - 1] + "…") if len(s) > n else s

    lines = []
    if result["errors"] and not result["chats_scanned"]:
        lines.append(L["no_data"])
    elif not replies:
        lines.append(L["none"].format(name=name, sent=sent))
    else:
        r_msg = [r for r in replies if r["class"] == "msg"]
        r_st = [r for r in replies if r["class"] == "status"]
        r_old = [r for r in replies if r["class"] == "old"]
        bits = []
        if r_msg:
            bits.append(L["bit_msg"].format(n=len(r_msg), u=result["unique_quoted"]))
        if r_st:
            bits.append(L["bit_status"].format(n=len(r_st)))
        if r_old:
            bits.append(L["bit_old"].format(n=len(r_old)))
        lines.append(L["header"].format(n=len(replies), bits=", ".join(bits), sent=sent))
        ex = sorted(r_msg, key=lambda x: x["ts"]) + sorted(r_st, key=lambda x: x["ts"])
        parts = []
        for r in ex[:5]:
            if r["class"] == "msg":
                own = short(r.get("own_body") or (L["media"] if r.get("own_has_media") else "—"), 30)
                parts.append('%s: "%s" ← %s' % (r["replier"], short(r.get("body") or L["media"], 40), own))
            else:
                parts.append('%s %s: "%s"' % (r["replier"], L["tag_status"], short(r.get("body") or L["media"], 40)))
        if parts:
            lines.append(L["examples"] + " | ".join(parts))
    out = "\n".join(lines)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
