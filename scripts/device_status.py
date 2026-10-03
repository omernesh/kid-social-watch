#!/usr/bin/env python3
"""device_status.py — per-child line/session status from the Chatlytics node.

Detects: WORKING (connected) / STOPPED (disconnected) / MISSING (not found).
Used for (a) a status line in daily reports, (b) a status JSON for a web
dashboard, (c) optional transition notifications (muted by default in the
reference deployment — disconnect notices may come from the platform instead).

Config (default ~/.hermes/kid-social-watch.json, override --config or
KID_WATCH_CONFIG; shared with the other kid-watch scripts). No secrets in the
config — the MCP token is read at runtime from the environment or an env file:

  {
    "children": {"alex": {"session": "alex_line", "name": "Alex"}},
    "site_dir": "/srv/kid-site",              # optional — dashboard data dir
    "mcp_url": "https://mcp.lextrove.com/mcp",# optional — default shown
    "mcp_env_file": "~/.hermes/.env",         # optional — default shown
    "mcp_env_key": "MCP_LEXTROVE_API_KEY"     # optional — default shown
  }

Status JSON is written to <site_dir>/data/device-status.json when site_dir is
set, else ~/.hermes/data/device-status.json.

Usage:
  python3 device_status.py --all --write            # refresh the status JSON (dashboard)
  python3 device_status.py --all --write --notify   # same + print ONLY status transitions (silent-cron pattern)
  python3 device_status.py <child> --line           # one-liner for the daily report
  python3 device_status.py <child>                  # JSON for one child
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = os.path.expanduser("~/.hermes/kid-social-watch.json")
IL = ZoneInfo("Asia/Jerusalem")


def load_cfg(path):
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return json.load(f)


def token_for(cfg):
    key_name = cfg.get("mcp_env_key") or "MCP_LEXTROVE_API_KEY"
    if os.environ.get(key_name):
        return os.environ[key_name]
    env_file = os.path.expanduser(cfg.get("mcp_env_file") or "~/.hermes/.env")
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                if line.startswith(key_name + "="):
                    return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return ""


def parse_payloads(data):
    out = []
    d = (data or "").strip()
    if d.startswith("{"):
        try:
            out.append(json.loads(d))
        except Exception:
            pass
    if not out:
        for line in data.splitlines():
            if line.startswith("data:"):
                p = line[5:].strip()
                if p and p != "[DONE]":
                    try:
                        out.append(json.loads(p))
                    except Exception:
                        pass
    return out


def unwrap(msgs):
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if "result" in m:
            r = m["result"]
            if isinstance(r, str):
                try:
                    return json.loads(r)
                except Exception:
                    return {"raw": r[:400]}
            if isinstance(r, dict) and "content" in r:
                for c in r["content"]:
                    t = c.get("text", "")
                    try:
                        return json.loads(t)
                    except Exception:
                        return {"raw": t[:400]}
            return r
        if "error" in m:
            return {"mcp_error": m["error"]}
    return {"error": "no result"}


def mcp_tool_call(url, tok, tool, args):
    headers = {
        "Authorization": "Bearer " + tok,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "curl/8.12.1",
    }

    def _call(method, params, h):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=150) as r:
            return r.read().decode("utf-8", "replace"), (r.headers.get("mcp-session-id")
                                                         or r.headers.get("Mcp-Session-Id"))

    data, sess = _call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "device-status", "version": "1.0"}}, headers)
    if sess:
        headers["mcp-session-id"] = sess
    _call("notifications/initialized", {}, headers)
    data, _ = _call("tools/call", {"name": tool, "arguments": args}, headers)
    return unwrap(parse_payloads(data))


def fetch_sessions(cfg):
    """list_sessions with one retry (cold-cache 503s are known)."""
    url = cfg.get("mcp_url") or "https://mcp.lextrove.com/mcp"
    tok = token_for(cfg)
    last = None
    for _ in range(2):
        try:
            res = mcp_tool_call(url, tok, "list_sessions", {})
            sessions = (res or {}).get("data")
            if isinstance(sessions, list):
                return sessions
            last = res
        except Exception as e:
            last = {"error": str(e)}
        time.sleep(3)
    raise RuntimeError("list_sessions failed: %s" % str(last)[:300])


def summarize(cfg, sessions):
    out = {}
    for kid, meta in (cfg.get("children") or {}).items():
        sname = meta.get("session") or ""
        heb = meta.get("name") or kid
        s = next((x for x in sessions if x.get("session_name") == sname), None)
        if s is None:
            s = next((x for x in sessions
                      if kid in str(x.get("display_name") or "").lower()), None)
        if s is None:
            out[kid] = {"heb": heb, "found": False, "status": "MISSING", "working": False,
                        "since": "", "session": sname}
            continue
        st = str(s.get("status") or "?").upper()
        lsa = s.get("last_status_at") or ""
        since = ""
        if lsa:
            try:
                dt = datetime.fromisoformat(str(lsa).replace("Z", "+00:00")).astimezone(IL)
                since = dt.strftime("%d.%m בשעה %H:%M")
            except Exception:
                since = str(lsa)[:16]
        out[kid] = {"heb": heb, "found": True, "status": st, "working": st == "WORKING",
                    "since": since, "session": s.get("session_name"),
                    "display_name": s.get("display_name"), "last_status_at": lsa}
    return out


def line_for(k):
    # Hebrew report line — adjust the strings for other languages.
    if k.get("working"):
        return "📱 מכשיר (Chatlytics): מחובר ✓"
    if k.get("found"):
        return ("📱 מכשיר (Chatlytics): ⚠️ מנותק — הסשן במצב %s%s"
                % (k.get("status"), (" מאז " + k["since"]) if k.get("since") else ""))
    return "📱 מכשיר (Chatlytics): ⚠️ הסשן לא נמצא במערכת — לבדוק ידנית"


def status_path_for(cfg):
    site_dir = cfg.get("site_dir")
    if site_dir:
        return os.path.join(os.path.expanduser(site_dir), "data", "device-status.json")
    return os.path.expanduser("~/.hermes/data/device-status.json")


def main():
    args = sys.argv[1:]
    config = os.environ.get("KID_WATCH_CONFIG") or DEFAULT_CONFIG
    if "--config" in args:
        i = args.index("--config")
        config = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 1
    cfg = load_cfg(config)
    write = "--write" in args
    notify = "--notify" in args
    kids_only = [a for a in args if not a.startswith("--")]
    sessions = fetch_sessions(cfg)
    kids = summarize(cfg, sessions)
    now = datetime.now(IL)

    if write:
        status_path = status_path_for(cfg)
        os.makedirs(os.path.dirname(status_path), exist_ok=True)
        prev = {}
        try:
            with open(status_path, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            pass
        payload = {"checked_at": int(time.time()),
                   "checked_at_str": now.strftime("%d.%m.%Y %H:%M"), "kids": kids}
        tmp = status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, status_path)
        if notify:
            lines = []
            for kid, cur in kids.items():
                old = (prev.get("kids") or {}).get(kid) or {}
                if not old:
                    continue
                if old.get("working") and not cur.get("working"):
                    st = cur.get("status") or "?"
                    since = cur.get("since") or ""
                    tail = " — הסשן במצב " + st + ((" מאז " + since) if since else "")
                    lines.append("⚠️ המכשיר של %s התנתק מ-Chatlytics%s" % (cur["heb"], tail))
                elif old.get("working") is False and cur.get("working"):
                    lines.append("✅ המכשיר של %s חזר להיות מחובר ל-Chatlytics (%s)"
                                 % (cur["heb"], now.strftime("%d.%m %H:%M")))
            if lines:
                print("\n".join(lines))
        if not notify:
            print("saved %s" % status_path)
            for kid, k in kids.items():
                print("%-8s %-25s %s" % (kid, k.get("status"),
                                         ("since " + k["since"]) if k.get("since") else ""))
        return 0

    if kids_only:
        kid = kids_only[0].lower()
        if kid not in kids:
            print("unknown child: %s" % kid)
            return 1
        if "--line" in args:
            print(line_for(kids[kid]))
        else:
            print(json.dumps(kids[kid], ensure_ascii=False, indent=1))
        return 0

    print(json.dumps(kids, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
