# kid-social-watch

A template Hermes skill for daily social-wellbeing monitoring of a child's WhatsApp — powered by [Lextrove](https://lextrove.com). Pulls the day's activity, checks for red flags (bullying, grooming, stranger danger, social exclusion), tracks volume trends, and delivers a compact daily report.

## What it does

Every day (or on demand), the skill:

1. Pulls the child's DM + group messages for the day from Lextrove
2. Compares volume week-over-week and month-over-month
3. Checks every message against a 4-category red-flag checklist (behavioral shifts, stranger danger, sextortion, cyberbullying)
4. Compiles a structured report: summary, red flags, trends, close contacts, active groups, verdict
5. Detects direct (quoted) replies to the child's own messages — an interaction marker that their messages aren't ignored (`scripts/kid_replies_marker.py`, a deterministic hub-side detector)

Example header:

```
📊 DAILY REPORT — Alex | 22.08.2026
```

## Why a template?

This repo is deliberately generic — **no secrets, no PII, no real names, phone numbers, session names, or job IDs**. Every value (child name, JID, Lextrove session, language, delivery target, schedule) is supplied at runtime when the parent/carer sets up the cron job.

## Install

```bash
git clone https://github.com/<you>/kid-social-watch.git
ln -sfn "$PWD/kid-social-watch" ~/.hermes/skills/kid-social-watch
```

Requires a Hermes installation with the Lextrove MCP server configured.

## Usage

Ask your agent: *"how was Alex's day socially?"* — the skill resolves the session, pulls the day, and produces the report.

### Deep-dive (ad hoc)

When a situation needs network context — social pressure around an event, sudden DMs from a group-only contact, exclusion dynamics, "who are the leaders in this circle?" — the companion methodology lives in the `social-graph-community-detection` skill, and the algorithm runner ships here: `scripts/social_graph_leiden.py` runs a Leiden resolution sweep + Louvain cross-check + betweenness brokers + a stability verdict over a weighted interaction graph (`pip install leidenalg igraph`). **Ad hoc only — never in the daily cron** (graph builds are compute-intensive; use them when a real question exists).

### Daily cron

The skill documents a daily cron recipe (evening schedule, staggered across multiple children). **Not pre-enabled** — create it per child when you want it. Pin a cheap capable model for daily runs.

## Files

```
SKILL.md                            # the skill definition + full runbook
README.md                           # this file
scripts/kid_replies_marker.py       # Phase 5 direct-replies detector
scripts/social_graph_build.py       # hub-based graph builder (feeds the Leiden runner)
scripts/social_graph_leiden.py      # ad-hoc community detection (Leiden/Louvain)
```

## License

MIT — steal it, adapt it, ship it.
