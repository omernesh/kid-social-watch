---
name: kid-social-watch
description: "Daily social-wellbeing watch for a child's WhatsApp — pull today's activity from Lextrove, flag red flags (grooming, bullying, stranger danger), report trends. Template — no secrets, no PII."
category: devops
metadata:
  tags: [whatsapp, lextrove, safety, children, monitoring, cron, template]
  related_skills: [whatsapp-integration, lextrove-analytics-queries, social-graph-community-detection]
---

# Kid Social Watch (Template)

Daily social-wellbeing monitor for a child's WhatsApp activity, powered by Lextrove. Detects mistreatment, declining relationships, and unusual patterns — then reports in a compact daily digest.

This is a **template skill**: every value (child's name, session, JID, delivery target, language) is supplied at runtime. **No secrets, no PII, no real names or numbers in this repo.** For the parent/carer's own deployment, all placeholders below are filled in at the time the cron job is created.

## When to use

- A parent/carer asks to "keep an eye on X's messages to make sure everything's OK" — recurring need
- Setting up (or maintaining) the daily watch cron for a child
- One-off "how was X's social day?" check

## Runtime configuration

| Variable | Example | Notes |
|---|---|---|
| `CHILD_NAME` | `Alex` | Display name — used in the report header and `resolve_entity` |
| `CHILD_JID` | `972501234567@s.whatsapp.net` | Canonical JID from `resolve_entity` → `identifier` (example, not real) |
| `LEX_SESSION` | `alex_primary` | The Lextrove session for the child's line — discover via `list_sessions`, match by phone number (session names can be masked!) |
| `PARENT_LANGUAGE` | `en` | Report language. User-specified; **default English** when not specified. Any language the model supports |
| `DELIVERY_TARGET` | `telegram:<chat_id>[:thread_id]` | Where the daily report goes |
| `SCHEDULE` | `0 20 * * *` | Local evening, after school/work wraps up |
| `TZ` | `America/New_York` | Timezone for daily windows and trends |
| `KID_WATCH_CONFIG` | `~/.hermes/kid-social-watch.json` | Optional — config for the hub-side tools (Phase 5 replies marker, Phase 6 monthly graph). Schema in `scripts/kid_replies_marker.py` docstring |

## Pipeline (6 phases)

### Phase 1 — Pull today's messages
```
search_messages(session=<LEX_SESSION>, chat_type="dm",    from=TODAY, to=TODAY, limit=100)
search_messages(session=<LEX_SESSION>, chat_type="group", from=TODAY, to=TODAY, limit=100)
```
Note: `list_messages` does NOT accept `chat_type` (schema rejects it) — for the DM/group split use `search_messages` with `chat_type` and no `query` (filter-only dump, newest-first; `query="*"`/empty fails). For per-person or single-chat pulls use `list_messages` with `sender_id`/`contact_name`/`chat_id`/`group_name`. Analyze for: bullying, arguments, social exclusion, sudden topic changes, unusual silence.

### Phase 2 — Volume trends (WoW + MoM)
```
get_volume_analytics(session=<LEX_SESSION>, endpoint="counts-daily", period="7d")
get_sender_analytics(session=<LEX_SESSION>, endpoint="rankings",    period="7d")
```
Compare: this 7d vs the 7d before it. This month vs last month.

### Phase 3 — Per-contact shifts
For each top contact the child DMs, compare volume across periods. Flag if a previously frequent contact drops off. (Note: DM-scoped analytics can return all zeros for some contacts — corroborate with `list_messages` if the numbers look wrong.)

### Phase 4 — Compile report
Format in `PARENT_LANGUAGE` (see Report format below). Cite evidence for every flag.

### Phase 5 — Direct replies marker
Run the shipped detector: `python3 scripts/kid_replies_marker.py <child_key>` (deterministic, hub-side, ~10s; children configured in the `KID_WATCH_CONFIG` file — see the script's docstring for the schema, incl. report `lang`). It reports quoted direct replies to the child's messages received in the window, split into message replies / status replies / earlier-message replies, with examples and cached LID/name resolution. Embed its output VERBATIM as an additional bullet in the engagement section of the report (Hebrew layout: under ⚡ מעורבות). On tool failure, note the marker as unavailable in the report language and continue — never block the report on it.

**How it works (detection facts):** chatlytics hub message rows carry `replyTo = {id: <stanza>, participant: <quoted author LID>, body: <quoted text>}`; a direct reply to the child ⇔ `replyTo.participant == child LID` OR `replyTo.id ∈ child's sent-message stanzas` (stanza extractable from own-message ids like `true_<chat>_<stanza>`). The same stanza quoted across ≥2 different chats = a status reply (per-chat sends get unique stanzas — a cross-chat collision means a status/broadcast). Lextrove's own `quoted_message_id` is NOT usable for this — it is a WAHA-format id that does not join to row UUIDs, and `get_message` rejects non-UUID ids. Hub access: `POST <hub>/api/v1/actions {action,params,session}` + `GET <hub>/api/v1/messages?chatId=…&session=…&limit=N` (top-level list, newest-first; `status@broadcast` returns empty — statuses themselves are not served).

### Phase 6 — Monthly community-graph scan (1st of each month only)
Run `python3 scripts/social_graph_build.py <child_key> --run` — hub-based: builds the child's last-30-days weighted interaction graph (DM pairs `3·log1p` + shared-group co-activity `log1p(min)`) and executes the Leiden/Louvain runner (`scripts/social_graph_leiden.py`: resolution sweep + cross-check + betweenness brokers + stability verdict). ~30s once the LID/phone id-map is cached. **Resource-intensive — run it ONLY on the 1st of the month, never on other days.** Embed the core output as a "🕸️ monthly communities" block in the report (before the month's deleted-messages block, or before the verdict): circles count, top brokers, and the child's stable group. On failure, note it as unavailable in one line and continue.

## Deep-dive escalation — community detection (ad hoc)

When a flag needs NETWORK context — social pressure around an important event, a sudden DM from a group-only contact, exclusion dynamics, "who holds social power in this circle?" — escalate to the ad-hoc community-detection deep-dive. Methodology (edge-weight recipe, interpretation playbook): the `social-graph-community-detection` skill. Runners ship here: `scripts/social_graph_build.py` (hub-based graph builder) and `scripts/social_graph_leiden.py` (solver).

Rules: **ad hoc or the monthly scan only — never on regular watch days** (graph builds are compute-intensive; use a bounded node set and only when a real question exists). Graph metrics are hypotheses — verify against message content before reporting.

## Red flag checklist

Every daily report must check messages against ALL four categories and cite evidence:

🚩 **Behavioral & communication shifts:** vanishing/disappearing messages or hidden vault apps; consistent late-night/early-morning texting; panic when away from phone; unfamiliar code words/slang/emoji; panic-deleting chat histories or contact threads.

🚩 **Stranger danger & grooming:** contact with clearly older adults or people not met in person; isolation tactics ("don't tell your parents", "our special secret"); gifts/favors (game currency, gift cards, physical packages); compliance testing (small favors escalating to secrets/boundary-pushing); platform hopping (public app/game → private messaging).

🚩 **Sexual exploitation / sextortion:** photo requests ("selfies", "outfit checks"); body talk (unusual focus on appearance/weight/sexual topics); threats/blackmail.

🚩 **Cyberbullying & social exclusion:** cruel teasing (appearance, intelligence, social status); targeted exclusion from group chats or gossip screenshots; encouraging self-harm or self-deprecating humor hinting at depression.

## Report format

Render the entire report in `PARENT_LANGUAGE` (default English) — header, labels, and verdict included. The user can request any language at runtime.

```
📊 DAILY REPORT — <Child Name> | <DD.MM.YYYY>

🤖 Summary:
[2-3 sentences on social activity, overall tone]

🚩 Red flags:
[Description or "None — normal"]

📈 WoW: [% change vs last week]
📈 MoM: [% change vs last month]

👥 Close contacts:
[Who's up, who's down]

💬 Active groups:
[Which groups were busy]

⚠️ Verdict: [Normal / Monitor / Needs attention]
```

- Header is exactly: `📊 DAILY REPORT — {CHILD_NAME} | {DD.MM.YYYY}` — translated into `PARENT_LANGUAGE` (e.g. Spanish: `📊 INFORME DIARIO — ... | 22.08.2026`)
- Section labels (🤖 Summary, 🚩 Red flags, 📈 WoW/MoM, 👥 Close contacts, 💬 Active groups, ⚠️ Verdict) are translated with the report
- **Hebrew deployments (layout approved 2026-09-09):** use the MANDATORY table layout in `templates/daily-report-hebrew-table.md` — מגמות / שולחים פעילים / קבוצות פעילות must be markdown pipe tables, never bullets. Embed the template in the cron prompt verbatim (cron sessions have no chat context).

## Detection heuristics

1. **WhatsApp Status vs DM** — a contact with many media messages and `chat_id: "status@broadcast"` = status updates, NOT DMs. Harmless. In sender rankings a status-only contact (no DM and no group rows) inflates message_count — verify with a `chat_type="status"` pull and label "statuses only", never "groups only".
2. **Volume drop >40% WoW** — investigate: social exclusion, vacation, or device issues.
3. **Sudden contact disappearance** — a top-3 contact dropping to near-zero in a week gets flagged.
4. **Negative language** — scan for bullying terms in the child's language; include a localized term list.
5. **All-media, no-text contacts** — normal for teens, but check content if volume is very high.
6. **Late-night activity** — note messages 01:00–05:00 local, don't necessarily alarm (irregular sleep is common, especially in summer).
7. **Revoked messages** — Lextrove retains deleted-for-everyone messages (`is_deleted=true`). A monthly scan of revoked messages can surface bullying/social-anxiety signals: someone sending and quickly deleting.
8. **Social pressure on important events** — look at the WIDE picture, not isolated flags: (a) a sudden DM from someone who normally doesn't DM the child (esp. a socially influential peer) is itself anomalous; (b) pressure to move/change a significant event date (birthday, party), including ultimatums like "don't make us choose between X and Y"; (c) clusters of hesitant RSVPs ("maybe") often trace back to a date conflict between parallel events, not indifference. Cross-reference the child's weeks-long plans and known-important events when evaluating pressure.
9. **Power dynamics** — when a socially dominant figure pushes a child around something the child cares deeply about, surface it in the report as a wellbeing flag with quoted evidence.
10. **Peer mentions of unexplained absences** — e.g. a classmate asking "why weren't you at school yesterday": not one of the four red-flag categories, but surface it as a low-key parent note unless an obvious explanation (illness, known trip) is present.
11. **Community-graph continuity (monthly)** — compare the monthly circles/brokers with previous months (dated specs are kept: `social_graph_<child>_<YYYYMM>.json` in the state dir): a top broker vanishing, the child's stable group shrinking, or new bridges appearing are structural signals worth a note — verify against message content before reporting.

## Daily cron (optional — NOT pre-enabled)

```
cronjob create \
  schedule='0 20 * * *' \
  name='social-watch-<child>' \
  skills=['kid-social-watch'] \
  prompt='Daily social watch for <CHILD_NAME> (session <LEX_SESSION>, JID <CHILD_JID>). Follow the kid-social-watch skill: run the pipeline phases and the Phase 5 replies marker (Phase 6 monthly scan ONLY on the 1st of the month), compile the report in <PARENT_LANGUAGE>, deliver to <DELIVERY_TARGET>.' \
  deliver=<DELIVERY_TARGET>
```

- Stagger multiple children (20:00 / 20:05 / 20:10) so Lextrove never runs heavy pipelines simultaneously.
- Pin the model in the job config if cost matters (daily jobs should use a cheap capable model).
- Cron prompts must be fully self-contained — cron sessions have no chat context.

## Pitfalls

- **Session names can be masked** — `list_sessions` display names may not match the child. Verify by phone number against the JID from `resolve_entity`. Never guess.
- **Always pass `session=<LEX_SESSION>`** to every analytics call — omitting it returns tenant-wide data (historical leak, fixed and verified).
- **`resolve_entity` → `identifier` is the JID** — pass it as `sender_id` / `chat_id`; the UUID is for profile tools.
- **`search_messages` can return empty for date windows** where `list_messages` returns full data — corroborate with volume analytics before concluding "quiet day".
- **DM analytics may return all zeros** for some contacts — known Lextrove limitation; fall back to `list_messages`.
- **Formatting:** markdown pipe tables render fine in Telegram (verified 2026-09-06) — use them for tabular data in chat delivery. Fall back to bullet lists only for very wide/multi-line content.
- **Verify delivery after manual runs** — check the job's `last_delivery_error`; async reports can fail silently.
- **Reply-time median inflated by one-sided media batches** — a burst of 30+ unanswered photos/videos gives every row the same later-reply gap and drags the median from minutes to hours (raw vs burst-aware medians differ by orders of magnitude). Collapse consecutive same-sender messages (≤10 min apart) into bursts; measure burst-end → child's next message; quote the median of bursts answered within ~6h, and report long/unanswered bursts separately.
- **This skill is public** — no real names, phone numbers, session names, or credentials. All config at runtime.
