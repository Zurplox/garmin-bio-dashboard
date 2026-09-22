# HANDOFF — Meridian Bio-Intelligence Terminal

**Read this first.** It is written for an AI agent (or a person) opening this
repository with no memory of the sessions that built it. Everything below is
either a fact about the current tree or a rule the codebase already follows;
nothing here is a plan that has not happened.

*Last updated: 2026-09-23, after the audit pass: a shortcut is a bare key, tonight's
beacon is clipped to its chart, and the no-emoji rule covers the model's words as well
as the page's own — each reproduced and measured on the running page before and after.
Check `git status` and `git log` for the true tip.*

---

## 1. What this is, in one paragraph

An automated, end-to-end encrypted personal health dashboard and analysis
pipeline for one athlete (the repo owner). A GitHub Action wakes at **08:00 SGT**,
pulls that athlete's data from their watch account, computes baselines and
verdicts, writes `data/biometrics.json`, encrypts it into
`data/biometrics.enc.json`, commits both, and GitHub Pages republishes the static
page. The page is **one file** (`index.html`) that decrypts the vault in the
browser with a passphrase and renders everything. There is no server, no build
step, and no database.

| | |
|:---|:---|
| Live site | https://zurplox.github.io/garmin-bio-dashboard/ |
| Repository | https://github.com/Zurplox/garmin-bio-dashboard (public) |
| Viewer passphrase | documented in `DASHBOARD_GUIDE.md` → *Dashboard Access & Security* (master-lock phrase is separate) |
| Language | Python 3.14 pipeline + one static HTML page (Tailwind CDN + Chart.js CDN) |
| Unit tests | `python -m unittest discover -s tests -t .` — 251 pass |
| Scheduler | `.github/workflows/daily_sync.yml` — `Daily Biometrics Sync & AI Analysis`, 00:00 UTC (08:00 SGT) |
| CI | `.github/workflows/tests.yml` — `Tests`, runs on push/PR |
| Rollback backup | `.github/workflows/backup_build.yml` — `Backup Build`, attaches a zip of the build to the persistent `build-backups` release on every push to main and whenever the daily sync finishes (GitHub starts no workflow from a push made with the default `GITHUB_TOKEN`, so the data commit needs the `workflow_run` trigger or the backup would lag). A release asset rather than a committed file, because release storage survives a force-push or a lost history and a committed zip would grow the repository for ever |
| Plaintext vault | `data/biometrics.json` is **gitignored**; only ciphertext ships |

---

## 2. Who the page is written for

**A normal person, not a clinician.** This is the most important product rule in
the repo, and the one most likely to be broken by a well-meaning change:

1. The scientific term stays, and its everyday meaning sits beside it — never
   instead of it. A reader who knows the jargon must not lose information.
2. **First mention carries the long form in brackets**: "Overnight HRV (Heart Rate
   Variability)", "resting heart rate (RHR)", "acute:chronic workload ratio
   (ACWR)", "blood oxygen (SpO₂)".
3. Every analysis is published as **two paragraphs**: the clinical one, then an
   explanation in everyday words underneath it. **There is no "In plain English:"
   label** — the explanation is simply the paragraph below. The rule engine
   writes that paragraph, so a language model can never drop it.
4. Details get explained too, not just conclusions: axis captions say what the two
   axes are, section headers carry a one-line subtitle saying what the section is
   about, and band names come from `DASHBOARD_GUIDE.md`'s own vocabulary.
5. An absent measurement reads `--` with a neutral tone. Never a reassuring
   invented word, never zero, never a guess.

---

## 3. Module map — one owner per concern

```
garmin_source.py ──► bio_analytics.py ──► clinical_engine.py
(the only watch I/O)   (pure arithmetic)    (verdicts: rules; model narrates)
        │                    │   └─ bio_correlate.py (channel relationships)
        │                    │   └─ bio_coach.py     (prescriptions per domain)
        └────────► sync.py (orchestrator) ──► data/biometrics.json
                     provenance.py (publish gate)      │ encrypt_data.py
                     bio_policy.py (every number)      ▼
                                              data/biometrics.enc.json ──► index.html
```

| Concern | Owner | The rule that keeps it honest |
|:---|:---|:---|
| Thresholds, bands, tones, labels, coaching targets, study citations | `bio_policy.py` | Nothing else may compare a metric against a literal. The payload ships bands already resolved. |
| Arithmetic: baselines, windows, strain, sleep need, readiness, injury risk | `bio_analytics.py` | Pure functions, no I/O, no environment. Decides positional windows too. |
| Watch access and parsing | `garmin_source.py` | Only module doing device I/O. Records live/fallback provenance as it fetches. |
| Live/fallback bookkeeping, publish gate | `provenance.py` | `DataQuality.publishable()` is the single gate. |
| Scores, bands, zones, illness risk, training target, analysis prose | `clinical_engine.py` | The rule engine is the ONLY author of the score. Gemini's number is kept as `model_score` and never published. Each analysis is published as clinical paragraph + explanation, joined by `PLAIN_PARAGRAPH_SEPARATOR`. |
| Relationships between the athlete's own channels | `bio_correlate.py` | Curated pairs only, with floors on paired days and coefficient magnitude. |
| Prescriptions ("what to do about it") | `bio_coach.py` | A domain with no measured history publishes **nothing** rather than a generic tip. |
| Orchestration, payload envelope, printing | `sync.py` | Wires the modules; owns `build_payload()`. |
| Encryption + status file | `encrypt_data.py` | Standalone; never imports the pipeline. |
| Rendering, interaction, presentation motion | `index.html` | Consumes resolved values. Its only decisions are layout, colour lookup from engine tones, and *when* to animate. |

`ARCHITECTURE.md` carries the same table with more detail, including the
three layers inside `index.html` (markup → render → motion). Read it before
restructuring anything.

---

## 4. Run and verify locally

Theme: the dashboard opens in **night mode** for a first visit, because the palette
is authored on the dark surface (gauge glows, glass cards, instrument tones); day
mode is the remap for when it is asked for. A saved choice in
`localStorage['meridian_theme']` wins, and the operating system preference is
deliberately not consulted — `ThemeDefaultTests` fails if `matchMedia` or
`prefers-color-scheme` reappears in the resolver. Both themes must be checked
after any visual change, and the toggle rebuilds the charts because Chart.js bakes
its colours in at construction.

```bash
# pipeline (writes data/biometrics.json, then encrypts to the vault)
python sync.py                 # needs watch credentials in .env / env vars
python encrypt_data.py

# checks — run all four before claiming a change works
node tests/check_frontend_syntax.mjs        # parses all inline <script> blocks
python -m unittest discover -s tests -t .   # engine, payload, guards
python -m py_compile *.py
python -c "import json;json.load(open('data/biometrics.json'))"
```

To *look* at the page, serve the repo root (the page fetches
`data/biometrics.enc.json` relatively) and unlock it with the documented
passphrase:

```bash
python -m http.server 8997 --bind 127.0.0.1
# then open http://127.0.0.1:8997/index.html
```

Unit tests never prove the page works. Every pass that changed the UI was
verified by reading real values out of the rendered DOM, not by re-reading the
diff.

---

`today_summary` is the top strip's owner: `build_today_summary(today, activities, capacity,
step_days, whoop, published_at)` resolves today's steps against the device's own goal,
the sessions inside the 24 hours ending at publication (with their categories and how
long ago the last one finished), and last night against the published sleep need. It
reads a `None` as absent — the strip prints `--` rather than a plausible number — and the
window it covers is published as `window_label` so the page never has to imply one.

## 5. What the payload carries

Top-level keys emitted by `sync.build_payload()`:
`athlete`, `baselines`, `capacity`, `clinical_intelligence`, `coaching`,
`correlations`, `data_quality`, `environment`, `fitbit`, `fitness`,
`garmin_signature`, `history`, `injury_risk`, `oxygen`, `policy`, `readiness`,
`today`, `updated_at`, `whoop`.

* `history` — `activities`, `daily_hrv`, `daily_rhr`, `daily_sleep`,
  `rhr_multi_year`. Every `daily_hrv` entry carries its own trailing 30-night
  baseline, `delta_pct`, `band`, `band_label` and `tone`, so any surface that
  shows a night takes its wording from the same owner.
* `clinical_intelligence` — score, band, tone, zone, briefing and the three
  analyses. Never contains a bare `plain_english` field; it is inside the string.
* `coaching` — `focus`, `cards` (one per training domain), `patterns`,
  `week`, `window_days`, `pattern_caveat`, `available`. Each card also carries the
  `visual` its words are about: seven finished days as bars (with `pct` already
  computed, an optional target line, and `null` for a day the device never recorded)
  or one `level` reading against its ceiling, and `null` when the domain has no
  history, which the page draws as empty stubs.
* `correlations` — `findings`, `tested_pairs`, `skipped`, `caveat`, the floors
  (`min_days`, `min_r`), the window, and `location` (home/away from device logs).
* `capacity.trend` — the movement card's owner: the seven **finished** days' average
  steps against the device's goal, the 30-day average, days that reached the goal,
  the change against the week before, the window it covers (`window.label`), the heart
  comparison on busier against quieter days (`heart`, with both day counts), and
  `plain`. Today is excluded on purpose, so the average does not shrink all morning.
* `data_quality` — per-metric `live`/`fallback` origin plus `publishable()`.

Some groups are deliberately thin in practice: blood oxygen is an on-demand
sensor (single-digit readings per 120 days), so those cards say what was recorded
rather than pretending to a nightly series.

---

## 6. Invariants — break one and a previous defect returns

These are not style preferences. Each one closed a real, user-visible bug.

1. **One owner per reading.** The recovery score, its band, tone and zone come
   from `clinical_engine`; the badge, the Autonomic State row, the pillar dot, the
   quadrant HUD and the HRV scrub HUD all read the payload's resolved band. A
   second vocabulary for the same number is how the page once showed `BALANCED`
   in green next to `Below Baseline` in amber.
2. **Absent stays absent.** No `|| 'BALANCED'`, `|| 'FRESH'`, `|| 0`. `--` with a
   neutral tone. In `index.html` that means `measured(value, suffix, prefix)` rather
   than `value || 54`: a plausible default is how a KPI card read `0.2` while the dial
   beside it said it had nothing to point at. In Python it means `_nullish` /
   `_truthy` / `_as_float`, never a bare `.get(key, default)` (see §6.8).
   (The quadrant scatter's point data left this list in #30; what remains is the
   in-engine `intel.recovery_score || 76` fallback, the briefing's own
   `hrv_last_night || 60`, and the snapshot's remaining record defaults — see §8.)
3. **No static claim beside a live number.** A header pill that always says
   `LIVE`, a tier badge baked to `ATHLETIC`, a colour hardcoded next to a
   word — all three shipped once. Derive both from the payload.
4. **The model never owns a number.** Gemini may write prose; `score_source`
   stays `deterministic` and the model's number is recorded as `model_score`. This
   was proven both with and without a key, and on the runner.
5. **No credential in the repo.** `SecretGuardTests` scans sources, docs,
   workflows and tests for `ghp_`, `github_pat_`, `AIza`, private keys. This repo is
   public and has secret scanning **and push protection** enabled, so a committed
   token would be detected and GitHub revokes a leaked token in a public repo — it
   would work for minutes and then silently stop. The refresh token therefore lives
   in one of two places, never here: a browser's `localStorage`
   (`garmin_github_token`), or the relay's own worker secret. Verified live: push
   protection validates credentials rather than matching their shape (a random
   `github_pat_`-shaped string was accepted, so a real one is exactly what it
   catches).

   There is no longer any token field in the page. A token enters a browser through
   a one-time link, `#token=<pat>`, read on load **and** on `hashchange` (pasting the
   link into an open tab changes only the fragment): it is stored, the fragment is
   stripped with `history.replaceState`, `#token=` alone forgets it, and anything
   that is not a `github_pat_`/`ghp_`/`gho_`/`ghs_` value is refused with a toast
   rather than stored.
6. **A sync can be started without a token in the page.** `relay/worker.js` holds
   the token as a worker secret and does one thing: dispatch `daily_sync.yml` on
   `main`. Set `SYNC_RELAY_URL` in `index.html` and `syncTriggerMode()` returns
   `relay`, the page POSTs there with no `Authorization` header, and there is no
   token to store on any device. The worker pins the workflow
   and ref, refuses any origin but `ALLOWED_ORIGIN`, holds a second request inside
   `MIN_INTERVAL_SECONDS` with a 429 (checked against GitHub's own run list, so it
   survives multiple isolates), and never returns GitHub's error body — only a
   status and a hint. Proven by `tests/check_relay.mjs` (13 checks against a stubbed
   GitHub, run in CI) and in a browser: the press reached the worker, which
   dispatched with `ref=main`, and a repeat inside the window came back 429 and told
   the reader so.
7. **One press, one run.** The Refresh button is single-flight and greys out with
   a cooldown; it never fires two dispatches. Do not test it by spamming the
   workflow — the owner has asked repeatedly for no extra GitHub runs. A refused
   press now also says so out loud (the `refuse` cue).
8. **Feedback has one vocabulary, in two senses.** `SOUND_CUES` in `index.html` names
   every cue and `playCue(name)` plays it; a call site may not choose a frequency of its
   own. Rising = opened, falling = closed, two rising = accepted, three rising = work
   started, the melody = work finished, one note = a step, a dull low pair = refused, a
   low pair = warning. Scrub, heat-map sweep and section jumps keep their own ladders
   because they are continuous, not discrete — and because they are continuous they have
   no haptic pattern either. `HAPTIC_CUES` names the same events with a tap (never longer
   than 160 ms), `pulseHaptic` is called from `playCue` and nowhere else, and the switch
   that silences the chirps stops the buzz: the header button says `Feedback ON/OFF`, while
   the stored key is still `garmin_sound_enabled`. Loudness is one policy —
   `MICRO_AUDIO_GAIN` (1.6) lifts every note, `MICRO_AUDIO_CEILING` (0.04) keeps it under
   a notification — applied inside `playMicroChirp` so the ladders are lifted too. A
   device with no `navigator.vibrate` just hears it; nothing stands in for the buzz.
   `TactileAudioTests` enforces all of it.

   **The interface is drawn, not typed.** A band, a state or a quadrant is a coloured
   `&#9679;` in the page's own palette, a control is a stroked SVG, and no emoji appears
   anywhere in the page (`InterfaceGlyphTests` fails on one). The two marks that remain
   sit outside the emoji ranges on purpose: the information mark that opens a dropdown,
   and the filled circle. Emoji as the *colour key* inside an explanation panel was the
   worst of it — it rendered differently on every platform and read as a chat message
   beside a clinical reading.

   **A revealed control is one that can work.** The fullscreen button is in the markup
   hidden and is revealed only where `fullscreenSupported()` is true (both spellings of
   the API plus `fullscreenEnabled`, because a frame can hold the method and not be
   allowed to use it); `F` says so with a refusal cue where it cannot. Its icon, spoken
   label and tooltip are re-read from the browser's own state on `fullscreenchange`, so it
   can never say "enter" while the page is fullscreen.
9. **Motion computes nothing.** `playVisuals()` reads what the render pass already
   wrote and re-applies it. Reduced-motion readers get final values, no hidden
   cards, no animation.
10. **A failed endpoint returns `None`, not a missing key.** `garmin_source` fills
   the fitness block with `None` when an endpoint answers with nothing, so every
   consumer must go through `_nullish` / `_truthy` / `_as_float`: a bare
   `.get(key, default)` sees the `None` and the default never applies. This took
   the whole sync down on 2026-09-22 — `calculate_fitbit_metrics` compared
   `acute_load` (`None`) against `80` and the run died before publishing — so
   `UnmeasuredWorkloadTests` now drives the readiness pillar and the narrative
   with that exact shape. It is also why the workload paragraph claims no ACWR
   band when the ratio is unmeasured: naming a band there was the same fabricated
   verdict in prose.

---

## 7. Delivered history (all merged, no PR open)

| PR | What it changed |
|:--:|:---|
| #1 | Split the 1,363-line `sync.py` into single-owner modules; publish provenance; guarded manual refresh; deterministic recovery score |
| #2 | Header state pill derived from data provenance (was static `LIVE`) |
| #3 | Plain-language explanations for every scientific term; info panels stopped quoting stale numbers |
| #4 | RHR tier badge gets an engine owner |
| #5 | HRV and ACWR status badges take their colour from policy |
| #6 | HRV gets one meaning across badge, dot, Autonomic row and quadrant |
| #7 | ACWR gets one vocabulary, taken from the user's own guide |
| #8 | The coaching layer (`bio_coach.py`) plus 13 cited evidence panels |
| #9 | The HRV scrub HUD takes the night's own band, tone and 30-night basis |
| #10 | Every reading explained in everyday words; the page animates as each visual reaches the fold |
| #11 | Gemini model fallback chain (`gemini-3.8` → `3.5`, overridable with `GEMINI_MODELS`) |
| #12 | Slower spring-eased motion with physics overshoot: staggered bars, night-by-night sleep chart, cascading heat map, square enlarged heat map, dial bounce, counting headline numbers |
| #13 | `None` from a failed endpoint no longer crashes the pipeline; an unmeasured workload ratio is reported as unmeasured instead of assumed |
| #14 | Every card renders `--` for an absent measurement instead of a plausible number; the fitness-age advantage badge gets a policy owner |
| #15 | Night mode is the default for a first visit; the operating system no longer picks the theme |
| #16 | **Today at a glance** strip: steps vs goal, the last 24 hours of training, and last night vs sleep need, all resolved by `build_today_summary` |
| #17 | Thinner wording on that strip: `under 1% of goal` and `2 sessions` instead of `0%` and `session(s)` |
| #18 | The pattern caveat gets one owner (`policy.CORRELATION_CAVEAT`), publishes with the correlation payload, and reads as a neutral statement — the `— not why` aside and the `is a guess` empty state are gone |
| #19 | The movement card becomes a week-and-month trend, built from the same daily channels the correlation lab uses, with the heart reading beside it and today's still-running day left out of the average |
| #20 | Bars fill from their own edge (`scaleX(0)` from the left, `scaleY(0)` from the bottom) instead of springing at 92% of their width; the five-pillar bars are primed by class with a failsafe that fills them if the reveal never comes; the quadrant chart rains its points in from above the plot |
| #35 | Fullscreen, haptics, louder feedback and no emoji. A header button fills the screen where the browser has the API (both spellings plus `fullscreenEnabled`, because a frame can hold the method and still not be allowed to use it) and is never revealed where it does not, with its icon, `aria-label` and tooltip re-read from the browser's own state on `fullscreenchange`; `F` toggles it. Every cue in `SOUND_CUES` gains a pattern in `HAPTIC_CUES` and `pulseHaptic` is called from the one place a cue is fired, so one switch (the header's `Feedback ON/OFF`) owns the sound and the buzz, patterns never run past 160 ms, and the continuous pitch ladders have no pattern at all; a device without `navigator.vibrate` just hears it. Every chirp is lifted by one gain (`MICRO_AUDIO_GAIN = 1.6`) under one ceiling (`MICRO_AUDIO_CEILING = 0.04`) inside `playMicroChirp`, so the ladders are lifted with the table. And the interface stops typing its icons: the explanation panels' coloured-circle band keys, the status chip's tick/warning, the metric cards' pictograms, the audio button and the lock screen's messages are all drawn or written now -- a test asserts no emoji remains anywhere in the page |
| #36 | A coach card's dashed target line states its own number. The 8,000 in the walking card lived only in the caption under the chart; the label now sits at the line's right-hand end, on whichever side of the line has room (`targetPct > 80` puts it below), and reads from the same published target the caption does -- 91% for this month's step goal, measured on the running page |
| #34 | Tonight's diamond on the quadrant map gets a beacon, and the chart callout that points at it arrives instead of appearing. The beacon is two rings (`beacon-out`) that leave the newest night once the map has settled -- measured at 4.99 s for 187 nights, `SCATTER_ARRIVAL_BEAT_MS * (n - 1) + SCATTER_SETTLE_MS` -- placed at the pixel the chart measured for that reading (`getDatasetMeta(last).data[0]`, read out of the chart rather than guessed from the scales) inside the chart's own container, so the rings sit on the dot at any window size; the element is reused across renders, and a timer left over from a replaced chart stands down. It has a cue of its own (`today`), because the map finishing its arrival is its own event. The callout arrives with one owner (`callout-in`, one `hover` cue for all four charts rather than a frequency per chart) and is placed from the box it actually renders -- measured 340 x 186, not the fixed 230 x 175 it used to assume, which opened a panel near the top of the window as if it were smaller than it is -- and it is a popover (`width: max-content`) instead of a block stretched to the width of the document |
| #33 | The sound vocabulary grows a cue per kind of movement (`grow`, `ring`, `count`, `chart`, `stagger`, `tick`), each fired by the primitive that owns the movement rather than by a call site, and each documented in the `SOUND_CUES` table. A *motion* cue merges its own repeats inside a window (`CUE_MERGE_MS`), because one panel filling is one movement and not a drum roll; the interaction vocabulary is never merged, so a press still answers every time it is made. The quadrant legend chip for the night being inspected lights up in that band's own colour, and the inspector's readings settle onto each new night (`hud-swap`) instead of swapping between frames |
| #32 | A fill plays when the reader reaches it. `primeGrow` used to publish a bar's value and play its spring in the same four-second failsafe, so every fill below the fold -- on a 16,000 px page, nearly all of them -- was spent before the reader got there. The two are separate promises now: the value is still published on the timer (a measured bar drawn as nothing stays the one forbidden outcome) and the spring is played only by the reveal. Measured on the running page: at 6.5 s the battery carried no spring class at all, and scrolling to it played the fill -- scale 0.70 → 1.05 → 1.0 while the label rolled 0 → 53%. The bar's `transition-all duration-1000` (the height tween that raced the spring, measured as a 2.7 px dip before the spring took over) is now `transition-colors`, and the cell carries a sheen the fill starts on its own beat. The same commit closes the hover jump: the quadrant inspector's label and sentence wrapped to one line more at a narrow viewport, growing the panel by 16 px and moving the chart -- and every reading below it -- out from under the cursor, so the cursor landed on a different night and the page kept jumping. `reserveHoverPanel` measures the tallest state each inspector can be in at the current width -- the four quadrants and the not-measured state for the scatter (`reserveScatterHudHeight`), the reading with and without its overlaid resting heart rate for the HRV panel (`reserveHrvHudHeight`), and, for the sleep and resting-heart-rate inspectors whose text is readings rather than prose, the one state where every field is at its widest (`reserveReadingHudHeight`, which finds each field's widest by running the scrub's own writer over the readings themselves) -- and reserves it as a `min-height`, with `hoverPanelReservations` giving one resize listener each panel's way to answer again. Measured at 821 px: 45 pointer steps across each of the four charts each held one canvas position, one panel height, one scroll position and an unchanged document height, and the sleep panel's reservation matched the tallest of its 30 real nights exactly (77 px reserved, 77 px measured). The scatter's reservation follows the window both ways -- 380 px wide reserves 218 px, 1000 px reserves 131 px, and back again |
| #21 | Every coaching card leads with its own reading drawn: seven finished days as bars (muted for a measured zero, dashed target line where policy has one) or a readiness level bar, empty stubs for a domain with no history. The coach's walking week moves to finished days so it quotes the same average as the movement trend card |
| #31 | Every night on the quadrant scatter arrives on a beat of its own instead of one beat per quadrant: the depth a dot comes forward from is now a per-night value (`scatterNightDepth`, a `WeakMap` read by the dataset's `pointRadius` accessor) and the arrival walks all 187 dots in draw order, promoting one per beat so the map comes forward dot by dot and tonight is the last one home. Measured on the running page: 187 promotions, 187 distinct start times, monotonic, 14-35 ms apart, last at 4.1 s, every dot landing on the size the render measured, none moving in x or y, none left at depth. The quadrant-wide `SCATTER_ARRIVAL_STAGGER_MS` is deleted and the per-dot beat is `SCATTER_ARRIVAL_BEAT_MS` |
| #30 | Tonight's point on the quadrant scatter is plotted only from readings the payload actually measured, and the two fields behind it stop being invented at the source: `build_today_snapshot` publishes `None` for an unmeasured overnight HRV and resting heart rate (it used to substitute 60 ms and 51.0 bpm) and publishes no population tier for an absent pulse. When there is no measured night the point is withheld and the chart says so -- no key in the legend, `--` and `NOT MEASURED` in the inspector on the neutral tone, and a sentence under the chart naming both measurements in plain English beside their scientific names. Proved by running the engine with the records present and absent, and by serving the page a vault with tonight's readings removed and then restored |
| #29 | The quadrant scatter's newest night is drawn in front of every earlier night (`order: -1` -- Chart.js paints the lowest order last, so without it tonight's diamond was painted first and buried under the cluster) and every night arrives from **depth** instead of falling in from above: the chart is built with every point at a far radius and each dataset's measured size is promoted on its own turn (322/529/742/1095/1264 ms apart, measured on the running page) with the shared overshoot bounce. The size is promoted rather than animated from a `from` value because Chart.js re-uses an animation config for every later transition -- measured: a `from` depth collapsed a hovered point from 4 px back to 0.55 px under the cursor. The arrival has one owner (`chart.arriveFromDepth`) played from two places: at the render, so every night reaches its measured size even if the reveal never comes (a chart caught at depth would draw readings as pinpricks), and by the reveal sweep when the reader scrolls to the chart -- resetting this chart is a no-op, measured as a single frame with every position and size unchanged, so the sweep now asks a chart for its own arrival first. Its point data also stops carrying the `hrv_status || 'BALANCED'` word nobody read |
| #28 | The heat map's tone moves off the focus view and onto the map itself: it sounds as the map loads while scrolling, the cue is renamed `reveal` to match when it plays, and opening or closing the enlarged view is silent |
| #27 | A re-render replays the motion of the panel it rebuilt: `primeMotion(scope)` becomes the single owner every pass applies, a rebuilt panel replays itself (`replayMotion`) instead of being replaced by plain nodes, and a second `renderDashboard` forgets the played marks so the readings it rewrote (bars, gauges, counters, charts) replay too. Primitives stand down from a second prime in the same pass rather than reading back their own zero, every animation finishes on the newest published value, and `replayAnimation` restarts an existing spring for a bar the render rewrites in place. Found and fixed on the real page: a reveal captured a Chart instance at prime time, and a render that destroyed it made `reset()` throw out of the sweep, leaving every primable behind that canvas unrevealed |
| #26 | One sound vocabulary (`SOUND_CUES` + `playCue`) replaces the frequencies chosen at each call site, and the silent controls speak: heat-map enlarge, lock, arming the emergency lockout, a Refresh that started, one refused by its cooldown, and one that failed. Continuous interactions keep their pitch ladders |
| #25 | The token field and its gear leave the header — a token now enters a browser through a one-time `#token=` link that stores it and strips itself (read on load and on `hashchange`), and `#token=` alone forgets it. The Refresh tooltip follows whichever trigger exists |
| #24 | The sync relay (`relay/`): a worker secret holds the token, so Refresh can start a sync with nothing pasted, nothing committed and no gear in the header. Worker logic executed in CI against a stubbed GitHub (`tests/check_relay.mjs`, 13 checks) and proven in a browser |
| #23 | Tactile audio is on for a first visit instead of off — the chirps are how a press reports back, and only an explicit `false` (the `M` toggle, or the header button) silences them |
| #37 | An independent audit's three defects, each reproduced on the running page before the fix and measured again after it. **A shortcut is a bare key.** The one `keydown` listener answered any key it recognised whatever was held with it, so `Ctrl+F` (the reader looking for a word on the page) took the full screen, `Ctrl+P` printed a second time and `Ctrl+R` — a reload — called `refreshVault`, which dispatches a real GitHub run in a browser holding a relay URL or a token. Ctrl, Meta and Alt now return before the key is read; Shift deliberately does not, because `Shift+R` is still a reader holding shift. Measured with every branch instrumented so nothing could fire: before, all six chords reached one (`Ctrl+f`, `Meta+f` and `Alt+f` each reached `toggleFullscreen`, `Ctrl+r` reached `refreshVault`, `Ctrl+p` reached `printClinicalReport`, `Ctrl+t` reached `toggleTheme`); after, all six are recorded with their modifiers and **zero** branches run, bare `f`/`t`/`r` still work, and typing into the real passphrase field reaches nothing. **Tonight's beacon is clipped to its chart.** The rings scale to 3.6x and the container did not clip them, so a ring left at a wide landing hung off the edge of the card and dragged the document sideways — a phone page scrolling horizontally because of a mark never meant to be seen outside its chart. Measured on the pre-fix build at a 320 px viewport: rings attached gave `scrollWidth` 561 against `clientWidth` 277, and 348 with the rings detached. `.interactive-canvas-container` now carries `overflow: hidden`, which changes where a ring is *seen* and never where it *lands* — its position is still the pixel `getDatasetMeta(last).data[0]` reports for tonight, equal at 320/380/768/1280 — and never when it plays (the arrival beat and the `today` cue are untouched). After: the identical state reads 352, the same as with the rings detached and the same with the rings deliberately held at 504 px on a 292 px viewport. **The no-emoji rule covers the model's words too.** It was enforced by a regex over `index.html`, which cannot see the briefing and analysis prose the payload carries, so a payload emoji reached the explanation panels (measured: 🚀 💤 ⚠️ rendered straight through). Every vault payload now enters through one boundary, `payloadFromVault(encPayload, password)` = `payloadWithoutEmoji(await decryptPayload(...))`, which walks the whole payload and strips only the pictogram ranges, so the page's own marks (U+25CF, U+24D8) pass by construction and a string with no emoji comes back byte for byte — and `decryptPayload` stays about cryptography. Measured: the same emoji payload rendered through the page's own writer shows 6 emoji in the panel before the boundary and 0 after it |
| #22 | No panel is a wall of words any more: a container marked `data-stagger` hands its children to the same reveal sweep (tiles, table rows, coaching cards, dossier blocks, briefing blocks), the glance strip tips in (`motion-tilt`), the lock screen rises in CSS, and the illness/glance numbers roll through their own sign. Two frame-dependency holes closed: the reveal sweep's rAF latch now has a timer, and a stalled count-up writes the published value back |

Since the last merge, `daily_sync.yml` has been dispatch-verified on `main`: the
runner fetches with the real key, publishes from the deterministic engine, commits
its data, and the published page loads the fresh vault.

---

## 8. Known open items (honest list)

* **The quadrant scatter no longer invents tonight.** #30 closed both its word (#29)
  and its coordinates: the point is built only from finite readings, and the snapshot
  that feeds it publishes `None` instead of 60 ms and 51.0 bpm. Serving the page a vault
  with tonight's two readings removed leaves tonight unplotted and says so.
* **The briefing still defaults the same field it now receives as `None`.**
  `index.html` computes `const hrv = today.hrv_last_night || 60` (and
  `baselines.hrv_30d || 55.3`) for the readiness delta and the AI briefing, so a payload
  without an overnight HRV still hands the briefing a plausible 60. The same shape sits
  in the snapshot's other record defaults (`sleep_score 78`, `steps 47`,
  `hrv_weekly_avg 57`, `hrv_status 'BALANCED'`) and in `intel.recovery_score || 76`.
  None of them reaches the quadrant chart any more; each is the next candidate for the
  same treatment.
* **Population advice still exists in a few info panels** ("Elite endurance:
  60–100+ ms"). It is labelled as context, but the page's own rule is
  compare-to-your-own-baseline.
* **Blood oxygen coverage is low** (single-digit readings per window). The cards
  say so; nothing should be inferred from them yet.
* **Coaching domains can read NOT MEASURED** for weeks (running, hiking) when no
  session of that type exists in the window. That is intended, not a bug.
* **A replay re-bounces rather than continues.** Replaying an element that is never
  replaced restarts its spring by removing the class, forcing a reflow and adding it
  back. If a render lands while that bar is mid-spring, the spring starts again from
  zero instead of picking up where it was — a brief second bounce, never a wrong value,
  which is the trade the restart buys.
* **The encryption caveat stands:** `DEFAULT_PASS` in `encrypt_data.py` is a
  fallback in a public repo, so anyone reading the source can decrypt the vault.
  Fixing that needs a repository secret and a passphrase rotation — see README.

---

## 9. How to land a change here

1. `git fetch origin && git switch -c <name> origin/main` — never commit straight
   to `main` (one batch did, and the PR the owner asked for had to be reported
   as missing).
2. Make the change in the owning module. If it needs a number, it belongs in
   `bio_policy.py`; if it needs arithmetic, `bio_analytics.py`; if it is a verdict,
   `clinical_engine.py`; if it is layout, `index.html`.
3. Run the four checks in §4, then read the real page and exercise the flow you
   touched — including the absent-data case and both themes.
4. Commit in the repo's style (imperative, benefit-led subject, e.g. *"Give the
   HRV scrub HUD the night's own band, not Garmin's word"*), push, open a PR whose
   body states the defect and the proof.
5. Drive CI green, squash-merge, realign local `main`, wait for Pages.
6. **Only then**, if the change touches the pipeline or the runner contract,
   dispatch `Daily Biometrics Sync & AI Analysis` **once** and watch it — a single
   run, never a batch. Re-reading the deployed page afterwards is part of the job.
7. Update this file, `README.md` (pass log) and `DASHBOARD_GUIDE.md` (metric
   guide) when the change alters behaviour a reader would notice.

Cost of getting this wrong: the athlete silently stops receiving fresh biometrics,
or sees two different verdicts for one night. Both have happened; both are why the
invariants in §6 exist.
