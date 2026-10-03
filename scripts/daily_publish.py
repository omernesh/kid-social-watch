#!/usr/bin/env python3
"""daily_publish.py — publish a child's daily report to the hosted report site.

Usage:
  python3 daily_publish.py <child> --report <path.md> [--date YYYY-MM-DD] [--config PATH]

  1. merges the report into <site_dir>/src/live/<child>-<YYYY-MM>.md
     (## YYYY-MM-DD section, replace-if-exists)
  2. rebuilds <site_dir>/data/daily-<child>-<YYYY-MM>.json via daily_archive.py
     (src/raw + src/live + cron output run files)
  3. renders <site_dir>/site/reports/<child>/<YYYY-MM>/daily.html (+ daily.json copy)
     — hides the monthly/graph tabs until those files exist (in-progress month)
  4. ensures a per-child·month share token (data/share-tokens.json)
  5. prints:  LINK: <site_domain>/s/<token>/daily.html#d-<YYYY-MM-DD>

The date is taken from --date, else the first DD.MM.YYYY in the report header,
else today (Asia/Jerusalem).

Config (default ~/.hermes/kid-social-watch.json, override --config or
KID_WATCH_CONFIG; shared with the other kid-watch scripts):

  {
    "children": {"alex": {"session": "alex_line", "name": "Alex"}},
    "site_dir": "/srv/kid-site",
    "site_domain": "https://kids.example.com"
  }
"""
import argparse
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = os.path.expanduser("~/.hermes/kid-social-watch.json")
IL = ZoneInfo("Asia/Jerusalem")
HEBREW_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
                 "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]


def load_json(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def load_cfg(path):
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return json.load(f)


def find_date(txt):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", txt[:600])
    if m:
        return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))
    return None


def merge_live(path, iso, text):
    txt = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    parts = re.split(r"(?m)^## (\d{4}-\d{2}-\d{2})\s*$", txt)
    out = [parts[0].rstrip("\n")] if parts and parts[0].strip() else []
    for i in range(1, len(parts), 2):
        d, body = parts[i], parts[i + 1].strip()
        if d == iso:
            continue
        out.append("## %s\n%s" % (d, body))
    out.append("## %s\n%s" % (iso, text.strip()))
    new = "\n\n".join(x for x in out if x) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kid")
    ap.add_argument("--report", required=True)
    ap.add_argument("--date", default="")
    ap.add_argument("--config", default=os.environ.get("KID_WATCH_CONFIG") or DEFAULT_CONFIG)
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    base = os.path.expanduser(cfg.get("site_dir") or "~/kidreports")
    domain = (cfg.get("site_domain") or "https://example.com").rstrip("/")
    children = cfg.get("children") or {}
    gen = os.path.join(base, "gen")
    data_dir = os.path.join(base, "data")
    live_dir = os.path.join(base, "src", "live")
    site = os.path.join(base, "site")

    kid = args.kid.lower()
    if kid not in children:
        print("unknown child: %s" % kid)
        return 1
    label_name = (children[kid].get("name") or kid)
    with open(args.report, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        print("empty report file")
        return 1
    iso = args.date or find_date(text) or datetime.now(IL).strftime("%Y-%m-%d")
    month = iso[:7]

    # 1. merge into the live month file
    lpath = os.path.join(live_dir, "%s-%s.md" % (kid, month))
    merge_live(lpath, iso, text)

    # 2. rebuild the daily json (raw + live + cron outputs)
    r = subprocess.run([sys.executable, os.path.join(gen, "daily_archive.py"), kid, month],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print("archive failed: %s" % (r.stderr or r.stdout)[-400:])
        return 2

    # 3. render daily.html
    spec = importlib.util.spec_from_file_location("mr", os.path.join(gen, "monthly_render.py"))
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    outdir = os.path.join(site, "reports", kid, month)
    os.makedirs(outdir, exist_ok=True)
    has_monthly = os.path.exists(os.path.join(outdir, "standard.html"))
    y, mo = month.split("-")
    label = "%s %s" % (HEBREW_MONTHS[int(mo) - 1], y)
    data = {"name": label_name, "month_label": label}
    with open(os.path.join(mr.TPL, "report.css"), encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(mr.TPL, "report.js"), encoding="utf-8") as f:
        js = f.read()
    with open(os.path.join(mr.TPL, "standard.tpl.html"), encoding="utf-8") as f:
        dtpl = f.read()
    dbody = mr.build_daily(data, kid, month, has_monthly=has_monthly)
    dout = (dtpl.replace("__TITLE__", mr.esc("%s · %s · דוחות יומיים" % (label_name, label)))
            .replace("__CSS__", css).replace("__KID__", kid)
            .replace("__BODY__", mr.wrap_tables(dbody)).replace("__SCRIPT__", js))
    with open(os.path.join(outdir, "daily.html"), "w", encoding="utf-8") as f:
        f.write(dout)

    dj = load_json(os.path.join(data_dir, "daily-%s-%s.json" % (kid, month)))
    if dj:
        with open(os.path.join(outdir, "daily.json"), "w", encoding="utf-8") as f:
            json.dump(dj, f, ensure_ascii=False)

    # 4. share token for this child+month
    tp = os.path.join(data_dir, "share-tokens.json")
    toks = load_json(tp, {}) or {}
    tok = next((t for t, v in toks.items()
                if v.get("kid") == kid and v.get("month") == month), None)
    if not tok:
        tok = secrets.token_urlsafe(16)
        toks[tok] = {"kid": kid, "month": month, "dir": "reports/%s/%s" % (kid, month)}
        tmp = tp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(toks, f, ensure_ascii=False, indent=1)
        os.replace(tmp, tp)

    days = len((dj or {}).get("days") or {})
    print("published %s %s: %d days" % (kid, month, days))
    print("LINK: %s/s/%s/daily.html#d-%s" % (domain, tok, iso))
    return 0


if __name__ == "__main__":
    sys.exit(main())
