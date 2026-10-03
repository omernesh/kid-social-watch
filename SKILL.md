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

### Phase 7 — Hosted monthly report portal (optional)
The monthly JSON outputs (per child: totals, daily series, DM/group splits, engagement, graph) can be published as a self-hosted report portal: a static RTL site with per-child/per-month HTML reports, print-perfect PDF export, and tokenized share links (`/s/<token>/`). Reference architecture:
- **Collector** — aggregates the month window (hub stats, DM/group split, engagement marker, Leiden graph) into one JSON per child; family-group stats merged in from a separate paginated fetch (`session=all` — required for shared groups).
- **Renderer** — string templates + a small inlined CSS/force-graph JS → `standard.html` + `graph.html`, then headless-Chromium print-to-PDF for both; writes a `manifest.json` (children × months) that the index page reads via `fetch`.
- **Auth/hosting** — tiny static server with HTTP Basic auth plus a share-token bypass for `/s/<token>/`; run under systemd and expose through an existing Cloudflare tunnel ingress.
- **Monthly automation** — one cron job on day 1: fetch → collect → render for all children; the agent phase drafts any missing narrative JSONs (summary / conclusions / recommendations, in the parent language) from the daily digests, re-renders, and messages the share links.

Template pitfalls worth remembering: escape literal `%` in %-formatted HTML strings; never use a replacement marker that is a substring of text already in the template (e.g. `__DATA__` inside `window.__DATA__`); flex charts need `align-items:stretch` on the row for percentage-height bars to render.

v2 additions (reference deployment, 2026-09-29):
- **Month-over-month deltas + flags strip** — two side-by-side cards under the KPIs. The strip parses daily-digest `VERDICT:`/`FLAG:` lines (flag when the verdict carries the explicit check level; ignore FLAG lines starting with the language's "none" word). Without previous-month data the deltas card shows a graceful empty state.
- **Daily-report modal** — a per-report `daily.json` archive (`{days: {YYYY-MM-DD: text}}`) written next to the HTML; bars / flag rows carry `data-d` and open the modal via `fetch`. The share-token route must include `daily.json` in its allowed-file list or the modal 404s on share links.
- **Guest accounts (time-boxed)** — `gen/guest_account.py add <user> <pass> <hours>` / `list` / `rm` manages temporary logins in `~/.hermes/data/kids-site-guests.json` (sha256-hashed, per-entry `expires_at`); the server checks expiry per request (expired creds fall through to 401) and prunes expired entries at boot. Verify expiry end-to-end with a short-lived account (e.g. `0.0012` h ≈ 4 s): expect 200 while fresh, 401 after it lapses, then `rm` it.
- **Logout button** — `/logout` responds 401 with the Basic realm header; the `🚪 יציאה` button (index header + report-page hero) fetch()es it with deliberately-wrong credentials so the browser drops its cached Basic-Auth login, then reloads to the auth prompt. Hidden on share-token pages together with the home button. Baseline best-effort (browser dependency): the prompt returns on next navigation; if a browser stubbornly keeps the session, a manual refresh settles it.
- **Favicon** — `gen/make_favicon.py` renders the site mark: a flat orange fox (Sammie's signature) on a purple→blue gradient square (Gaya/Yuval accent colors). Ships `favicon.svg`, multi-size `favicon.ico` (16/32/48 via ImageMagick `convert` from separately rendered PNGs), 32/192/512 PNGs, and a FULL-BLEED `apple-touch-icon.png` (rx=0 — transparent corners render black on iOS). Includes a dedicated simplified 16px SVG variant (bolder ears/eyes/nose) because the master's nose blurs away at 16px. Icons are linked with absolute paths (`/favicon.svg`) from every page (template + index) and the server whitelists them as PUBLIC paths (no auth, separate from the share-token set). Pitfall: delete the target PNG before the chromium screenshot — a stale file makes a failed re-render silently pass the existence check.
- **Mobile responsiveness** — a single `@media (max-width:720px)` block in `report.css` (hero stacks: column + 3-col tab grid + badge start-aligned; KPIs 2-col; `.two` stacks; smaller table/daily-text sizes; full-width search; SVG map via `aspect-ratio`). All tables get a `.tblwrap` scroll container at render time (`wrap_tables()` post-processor). CRITICAL lesson: if any element's min-content width exceeds the phone viewport, real Chrome (and Playwright `is_mobile`) EXPANDS the layout viewport (shrink-to-fit, e.g. 927px on a 390 phone) — media queries then never match and the page looks "messed up" on phones only. Verify with `gen/qa_mobile.py`: it sweeps every page × {390,375,360} × {plain,phone} and must report `innerWidth == viewport width` (no expansion), `overflow=False`, `badgeClip=False`, `heroDir=column`. Phones-only bugs do NOT reproduce in plain small viewports — always test the `phone` mode too.
- **Daily reports page** (`daily.html`) — full daily reports browsable per kid/month: newest first, weekday headers (`יום שני · 21.09`), client-side search box with an `X מתוך N דוחות` counter, and a light markdown pass for the card bodies (`**bold**`, `` `code` ``, pipe tables → `<table class="dt">` — daily digests contain pipe tables that otherwise leak raw `|` into the pre-wrap text). Pitfall: split table rows on UNESCAPED pipes only — the digests escape in-cell pipes as `\|` (e.g. `261 (19%) \| 964 (70%)`), and naive splitting creates phantom columns that spill outside the table borders. Generated by the renderer from `daily-<kid>-<month>.json`. The standard/graph tab bars get a third tab `📅 דוחות יומיים` (shown only when the daily archive exists), the daily modal appends a `📄 פתח בדף הדוחות היומיים` deep-link (`daily.html#d-<iso>`), the index card gets a green `dailylink` button driven by the manifest `daily: true` flag, and the tokenized share route must include `daily.html` in its allowed-file set. Pitfall: keep the inline search `<script>` closing tag literal — an escaped `<\/script>` swallows the rest of the document.
- **Hover tooltips everywhere** — `data-tip` attributes + one delegated tooltip element (avoid native `title`, it double-shows); cover all charts, KPI tiles, and card-level ⓘ icons that explain each card's purpose in plain language (users explicitly request "why is this card here").
- **Interactive network map** — deterministic layout at load, then drag nodes / wheel-zoom / pan via an SVG `<g>` transform; click-without-drag opens a per-entity panel (degree strength + top connections); double-click resets the view; labels get `pointer-events:none` so they don't block dragging.
- **Force-layout spacing (dense maps)** — a plain force sim leaves node circles overlapping on dense ego-networks (negative clearance — the actual symptom users report as "too crowded"). Fix in three moves, tuned by replicating the exact sim in Python against the real specs (fast iteration, no browser round-trips): (1) stronger repulsion + a collision floor inside the sim loop; (2) a post-scaling relaxation pass in SCREEN space enforcing `dist ≥ r_i + r_j + 16` with weak edge springs and boundary clamps — guarantees separation regardless of the fit-to-canvas compression; (3) labels only for top ~14 nodes by degree, alternating above/below by rank parity. Node radius `5 + 12.5·(deg/maxDeg)^0.62`, canvas ~1000×640. Verify NUMERICALLY in the browser (read `#net circle` cx/cy/r → min pair clearance must be positive), not by eyeballing.
- **Name map** (`name-map.json`) — hub chat lists + family-group aliases + a curated canonical table (all of a parent's pushName variants → one display identity) + Lextrove `resolve_entity` fallback for unresolved group JIDs. Consumed by BOTH the renderer (display labels) and the graph builder (node labels — replaces truncated `…123456` / `…pp.net` labels with names or phone-formatted numbers). Persist it across runs and rebuild it BEFORE collection so fresh graph builds get clean labels.
- **Pipeline order matters:** fetch family → build name map → collect (incl. graph) → peer baseline (`member_stats.py`) → merge family → daily archive → render → PDFs → manifest.
- **PDF export uses A4 landscape** (`@page{size:A4 landscape;margin:10mm}`) — the ~1060px-wide design clips horizontally on portrait A4 (only ~717px usable vs ~1046px usable in landscape). After any print-CSS change, re-render and verify with `pdfinfo` (expect 841.92×594.96 pts) plus a rasterized page check.
- **DM label resolution reality check** — hub message `pushName` covers only part of the contacts; the reliable source is Lextrove `list_contacts` (`display_name` aggregates the family phone address books). Reference script `gen/fetch_lx_contacts.py`: paginated bulk dump + per-number `search=<local form>` fallback for every still-raw id/label in the kid data files; merge by phone into the name map, never overwrite real names. The `lx_mcp_call.py` CLI now parses the server's plain-JSON responses (it previously assumed SSE); for bulk responses over ~12 KB bypass the CLI (its output is truncated) and use a direct-HTTP script.
- **Never aggregate chat participants by raw pushName** — one account posts under many profile names (six variants seen for a single parent: pet names, gmail label, session key…). Group messages by canonical sender phone/JID first, then resolve a display label (curated family table → name map → most-common raw name). This bug class must be checked anywhere per-person counts are shown (family card, top senders, community membership).
- **Never merge members by display name (same-name collision trap)** — two different members can present nearly the same name/style in one group (e.g. two girls with the same first name in one class). Name-based merging fabricates numbers — a stranger's volume can get attributed to the child and produce false claims ("the child is a central participant"). Ground truth for the child's own activity = `fromMe` rows served to the child's own session; verify each child's alias set (phone + LID forms) once, store it in the config, and treat name lookups as display-only.
- **Peer-comparison baseline** (`scripts/member_stats.py`) — per-member stats per top group over the month window: messages sent, replies received (quoted), conversation initiations (gap > 6h after the previous message). Outputs one baseline JSON per child/month; wire it into the monthly pipeline so narrative claims ("a central participant", "mostly a reader") are always backed by ranks and shares. Reply-rate (`replies received ÷ messages sent`) is often more telling than raw volume — a low-volume member can lead the group on how reliably her messages get answered.
- **"Vs peers" card (standard report)** — renders the latest `member-baseline` JSON as a card: per top group a header (`total · participants`), horizontal bars for the top peers with the child's row auto-inserted and highlighted, and a stats line (replies received + rank · initiations). Bars carry `data-tip` tooltips; names resolve through the same name map. Robustness rules: skip groups with no comparable data (no captured peer sends AND zero child sends), and return an empty string when no baseline file exists for the month — the card must hide gracefully, never break the render. Participant count = senders seen in the window (+ the child when they didn't send), and the child's row always displays so quiet children still see their own position.
- **Back-to-top button** — one shared JS snippet (report pages + index) injects a fixed circular button (accent gradient on child pages) that fades in once scrolled past `min(380px, 35% of scrollable height)` and smooth-scrolls to top; hidden in print CSS. Use a proportional threshold, not a fixed one — pages that barely scroll (e.g. an index on wide screens) never reach a fixed threshold, and the test must scroll to a real position (`scrollTo(0, 999999)`) rather than assume a page height.
- **PDF export naming** — downloads follow `<kid>-<MM>-<YYYY>-<reportType>.pdf` (e.g. `alex-09-2026-standard.pdf`): set the `download` attribute on every PDF link AND a `Content-Disposition: inline; filename="…"` header in the server, derived from the `/reports/<kid>/<YYYY-MM>/` path — the header covers direct-URL and share-token downloads that never go through a link. Verify with a real download event (`expect_download().suggested_filename`), not by reading the attribute alone. Note: `file://` pages can't `fetch()` the manifest, so page QA needs a local HTTP server.
- **Overlay labels swallow hover events** — an absolutely-positioned `inset:0` label over a chart (e.g. the donut's center text) intercepts every mouse event over the whole chart, killing tooltips; set `pointer-events:none` on the overlay. Verify tooltips with a REAL browser (playwright `mouse.move` + read `.tip` state + console errors), never by grepping the HTML for `data-tip` alone.
- **% -formatted strings: count placeholders vs args after EVERY edit** — mismatched counts raise `TypeError: not all arguments converted during string formatting` at render time (silent page regressions if the renderer continues). Render one child immediately after template edits and confirm the diff landed.

### Phase 8 — Live daily publishing + device status (portal add-ons)

**Live daily publishing.** `daily_publish.py <child> --report <file.md>`:
1. merges the composed report into a live per-month source file (`## YYYY-MM-DD` sections; same-date replaces). Sources merge order for the daily JSON rebuild: raw archive → live file → cron-run extraction (cron-extracted copies get appended footer lines — site link / device status — stripped).
2. renders `reports/<child>/<month>/daily.html`. For an in-progress month with no monthly report yet, synthesize a minimal `{"name","month_label"}` data object and thread a `has_monthly` flag through the tab/hero builders so the monthly/graph tabs hide until those files exist (else the page 404s on click).
3. ensures a per-child·month share token and prints the deep link `…/s/<token>/daily.html#d-<iso>`.

Daily prompt step (mandatory, in every child's daily job): save the composed report to a scratch file → run `daily_publish.py` → run `device_status.py <child> --line` → append to the delivered report at the very end: the device line + `🔗` site link; when the device is disconnected also add a warning right under the report header. Never describe a disconnect as "phone turned off" — say "session disconnected from Chatlytics since X; data missing".

**Device status.** `device_status.py` queries the analytics MCP `list_sessions` (raw HTTP JSON-RPC POST; token from env) for each child's session status (WORKING vs STOPPED/missing) and formats a Hebrew line. Modes:
- `--all --write` — refreshes the status JSON served at `/status.json` (auth-protected) and prints a human table.
- `--all --write --notify` — prints ONLY transitions (disconnect/reconnect). MUTED by default: Omer gets session-disconnect notices directly from Chatlytics, so the scheduled refresher runs WITHOUT `--notify` (dashboard freshness only). Re-enable only if he asks.
- `<child> --line` — the Hebrew one-liner for the daily report.

Portal UI: home page fetches `/status.json` for per-child status chips (red = disconnected, with "since" time); report pages inject a red banner when the child's session is not WORKING — skip the fetch on share pages (no auth there).

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
- **Editing %-formatted HTML strings** — count placeholders vs args after edits (mismatch → `TypeError: not all arguments converted during string formatting`); render one child end-to-end immediately after template edits.
- **Headless Chromium (snap builds) can't write into dot-directories** — screenshot to a non-hidden path, then move the file.
- **This skill is public** — no real names, phone numbers, session names, or credentials. All config at runtime.
- **Daily archive from cron run files — take the LAST report, not the first.** A run file embeds the report template early (copied from the cron prompt) and the real report later (final output). Extract the last report mark that carries a date in its header (`📊 דוח יומי` + `DD.MM.YYYY` within ~400 chars); taking the first occurrence serves template stubs (`[DD.MM.YYYY]`) for those days.
- **Month-boundary day-fill off-by-one.** For a closed month the window `end` sits at next-month 00:00 — a zero-day fill loop using `end.date()` adds a phantom day and `days_covered` becomes month_days + 1 (visible in KPIs and charts). Fill only up to the last included second of the month.
- **Mid-month draft narratives go stale.** If narrative JSONs were drafted during an in-month preview, their embedded numbers predate the final collection; the day-1 existence check alone will not update them. Refresh the numbers from the final JSONs and re-render before announcing a new month.
- **Closed-month verification pass (after collect + render):** zero `[DD.MM.YYYY]` placeholders in daily pages · KPI days `N/N` equals the calendar month length · no stale headline numbers in the HTML (grep a few) · every share-token page returns 200 · manifest and PDFs freshly written.
