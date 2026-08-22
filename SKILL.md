---
name: kid-social-watch
description: "Daily social-wellbeing watch for a child's WhatsApp — pull today's activity from Lextrove, flag red flags (grooming, bullying, stranger danger), report trends. Template — no secrets, no PII."
category: devops
metadata:
  tags: [whatsapp, lextrove, safety, children, monitoring, cron, template]
  related_skills: [whatsapp-integration, lextrove-analytics-queries]
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
| `TZ` | `Asia/Jerusalem` | Timezone for daily windows and trends |

## Pipeline (4 phases)

### Phase 1 — Pull today's messages
```
list_messages(session=<LEX_SESSION>, chat_type="dm",    from=TODAY, to=TODAY, limit=100)
list_messages(session=<LEX_SESSION>, chat_type="group", from=TODAY, to=TODAY, limit=100)
```
Use `list_messages`, NOT `search_messages` (FTS needs a topic query and is flaky with non-Latin text). Analyze for: bullying, arguments, social exclusion, sudden topic changes, unusual silence.

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

## Detection heuristics

1. **WhatsApp Status vs DM** — a contact with many media messages and `chat_id: "status@broadcast"` = status updates, NOT DMs. Harmless.
2. **Volume drop >40% WoW** — investigate: social exclusion, vacation, or device issues.
3. **Sudden contact disappearance** — a top-3 contact dropping to near-zero in a week gets flagged.
4. **Negative language** — scan for bullying terms in the child's language; include a localized term list.
5. **All-media, no-text contacts** — normal for teens, but check content if volume is very high.
6. **Late-night activity** — note messages 01:00–05:00 local, don't necessarily alarm (irregular sleep is common, especially in summer).
7. **Revoked messages** — Lextrove retains deleted-for-everyone messages (`is_deleted=true`). A monthly scan of revoked messages can surface bullying/social-anxiety signals: someone sending and quickly deleting.

## Daily cron (optional — NOT pre-enabled)

```
cronjob create \
  schedule='0 20 * * *' \
  name='social-watch-<child>' \
  skills=['kid-social-watch'] \
  prompt='Daily social watch for <CHILD_NAME> (session <LEX_SESSION>, JID <CHILD_JID>). Follow the kid-social-watch skill: run the 4 phases, compile the report in <PARENT_LANGUAGE>, deliver to <DELIVERY_TARGET>.' \
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
- **Formatting:** never use markdown pipe tables for chat delivery (render broken) — use bullet lists.
- **Verify delivery after manual runs** — check the job's `last_delivery_error`; async reports can fail silently.
- **This skill is public** — no real names, phone numbers, session names, or credentials. All config at runtime.
