#!/usr/bin/env python3
"""Peer-comparison baseline for a child's report — per-member stats per group.

usage: member_stats.py <child_key> [--month YYYY-MM] [groupJID...]
  (no groups → top-4 groups auto-picked from <data>/<child>-<month>.json)

Config (the same KID_WATCH_CONFIG used by kid_replies_marker.py) must carry per
child: session, and an `aliases` list of the child's verified identity forms
(phone and LID digits, e.g. ["972501234567", "123456789012345"]).

Ground truth for the child = `fromMe` rows served to the child's own session
(their sends). Peers = every other participant (non-fromMe rows, keyed by their
LID). Also per member: replies received (quoted), initiations (gap > 6h).
NEVER merge a member into the child by display name — many accounts post under
aliases, and two members can share a first name. Only the verified alias set.
Display names resolve via the name map → pushName → raw id.
Output: <data>/member-baseline-<child>-<month>.json
"""
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

CFG_PATH = os.path.expanduser(os.environ.get("KID_WATCH_CONFIG", "~/.hermes/kid-social-watch.json"))
CFG = json.load(open(CFG_PATH))
HUB = CFG["hub_base"].rstrip("/")
KEY = open(os.path.expanduser(CFG["key_file"])).read().strip()
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ_OFFSET = CFG.get("tz_offset_hours", 3)


def month_window(month):
    tz = timezone(timedelta(hours=TZ_OFFSET))
    y, mo = int(month[:4]), int(month[5:7])
    ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return (datetime(y, mo, 1, tzinfo=tz).timestamp(),
            datetime(ny, nmo, 1, tzinfo=tz).timestamp())


def get(url):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + KEY,
                                               "User-Agent": "curl/8.12.1"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode())


def load_nm():
    try:
        return json.load(open(os.path.join(BASE, "data", "name-map.json"), encoding="utf-8"))["by_jid"]
    except Exception:
        return {}


def main():
    args = sys.argv[1:]
    kid = args.pop(0)
    month = os.environ.get("MEMBER_STATS_MONTH", "2026-09")
    if "--month" in args:
        i = args.index("--month")
        month = args[i + 1]
        del args[i:i + 2]
    groups = args
    if not groups:
        try:
            d = json.load(open(os.path.join(BASE, "data", "%s-%s.json" % (kid, month)), encoding="utf-8"))
            groups = [g["id"] for g in (d.get("groups") or [])[:4]]
        except Exception as e:
            print("no groups given and auto-pick failed:", e)
            return 1
    S = CFG["children"][kid]["session"]
    aliases = set(CFG["children"][kid]["aliases"])
    nm = load_nm()
    win_start, win_end = month_window(month)
    result = {}
    for gid in groups:
        m = get(HUB + "/api/v1/messages?chatId=%s&session=%s&limit=6000" % (gid, S))
        raw = m if isinstance(m, list) else (m.get("messages") or [])

        def ts(x):
            return (x.get("_data") or {}).get("messageTimestamp") or 0

        def is_reaction(x):
            msg = (x.get("_data") or {}).get("message") or {}
            return isinstance(msg, dict) and isinstance(msg.get("reactionMessage"), dict)

        raw = [x for x in raw if win_start <= ts(x) < win_end]
        raw.sort(key=ts)

        kid_sent = 0
        peer_sent = Counter()
        lid_names = defaultdict(Counter)
        replies_recv = Counter()
        initiations = Counter()
        id2author = {}

        for x in raw:  # pass 1: sends + identity maps
            if is_reaction(x):
                continue
            dd = x.get("_data") or {}
            k = dd.get("key") or {}
            lid = (k.get("participant") or "").split("@")[0]
            ph = (k.get("participantAlt") or "").split("@")[0]
            isfm = bool(x.get("fromMe")) or bool(k.get("fromMe"))
            if isfm:
                kid_sent += 1
                continue
            ckey = lid or ph
            if not ckey:
                continue
            peer_sent[ckey] += 1
            id2author[x.get("id")] = ckey
            if lid and dd.get("pushName"):
                lid_names[lid][dd.get("pushName")] += 1

        prev_ts = None
        for x in raw:  # pass 2: initiations (gap > 6h)
            if is_reaction(x):
                continue
            t = ts(x)
            k = (x.get("_data") or {}).get("key") or {}
            isfm = bool(x.get("fromMe")) or bool(k.get("fromMe"))
            if prev_ts is None or t - prev_ts > 6 * 3600:
                if isfm:
                    initiations["KID"] += 1
                else:
                    author = id2author.get(x.get("id"))
                    if author:
                        initiations[author] += 1
            prev_ts = t

        for x in raw:  # pass 3: replies received
            if is_reaction(x):
                continue
            rt = x.get("replyTo") or {}
            part = (rt.get("participant") or "").split("@")[0] if isinstance(rt, dict) else ""
            if part:
                if part in aliases:
                    replies_recv["KID"] += 1
                else:
                    replies_recv[part] += 1

        def name_of(key):
            if key == "KID":
                return "**%s**" % kid
            for kk in (key + "@lid", key + "@s.whatsapp.net", key + "@c.us"):
                if kk in nm:
                    return nm[kk]
            if lid_names.get(key):
                return lid_names[key].most_common(1)[0][0]
            return key

        peers_ranked = peer_sent.most_common()
        kid_r = 1 + sum(1 for _, v in peers_ranked if v > kid_sent)
        kid_rr = replies_recv.get("KID", 0)
        kid_rr_rank = 1 + sum(1 for k, v in replies_recv.most_common() if k != "KID" and v > kid_rr)

        result[gid] = {
            "total": len(raw), "kid_sent": kid_sent,
            "kid_rank_sent": kid_r, "senders": len(peer_sent) + (1 if kid_sent else 0),
            "kid_share_pct": round(kid_sent / max(1, len(raw)) * 100, 1),
            "kid_replies_recv": kid_rr, "kid_rank_replies": kid_rr_rank,
            "kid_initiations": initiations.get("KID", 0),
            "top_sent": [{"name": name_of(k), "n": v} for k, v in peers_ranked[:12]],
            "top_replied": [{"name": name_of(k), "n": v} for k, v in replies_recv.most_common()[:12]],
        }
        print("== %s | msgs=%d | members=%d" % (gid, len(raw), len(peer_sent) + 1))
        print("   %s: sent=%d (rank #%d) = %.1f%% of traffic | replies-recvd=%d (#%d) | initiated=%d"
              % (kid, kid_sent, kid_r, result[gid]["kid_share_pct"], kid_rr, kid_rr_rank,
                 initiations.get("KID", 0)))

    path = os.path.join(BASE, "data", "member-baseline-%s-%s.json" % (kid, month))
    json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
