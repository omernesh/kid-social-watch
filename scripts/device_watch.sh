#!/bin/bash
# Silent refresher for the dashboard device-status JSON — no notifications.
# Reference deployment: run every 30 min from cron; empty stdout = silence.
# Allowlist: if you want disconnect/reconnect pings, add --notify below.
exec python3 "$(dirname "$0")/device_status.py" --all --write >/dev/null 2>&1
