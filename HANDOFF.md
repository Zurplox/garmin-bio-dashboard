# HANDOFF — Meridian Bio-Intelligence Terminal

**Read this first.** It is written for an AI agent (or a person) opening this
repository with no memory of the sessions that built it. Everything below is
either a fact about the current tree or a rule the codebase already follows;
nothing here is a plan that has not happened.

*Last updated: 2026-09-21, after the plain-language + motion pass (uncommitted at
the time of writing — check `git status` and `git log` for the true tip).*

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
| Unit tests | `python -m unittest discover -s tests -t .` — 141 pass before this pass, 146 after |
| Scheduler | `.github/workflows/daily_sync.yml` — `Daily Biometrics Sync & AI Analysis`, 00:00 UTC (08:00 SGT) |
| CI | `.github/workflows/tests.yml` — `Tests`, runs on push/PR |
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
8. **Sound has one vocabulary.** `SOUND_CUES` in `index.html` names every cue and
   `playCue(name)` plays it; a call site may not choose a frequency of its own. Rising
   = opened, falling = closed, two rising = accepted, three rising = work started, the
   melody = work finished, one note = a step, a dull low pair = refused, a low pair =
   warning. Scrub, heat-map sweep and section jumps keep their own ladders because
   they are continuous, not discrete. Volumes stay at or below 0.025 so a cue reads as
   feedback rather than a notification, and `TactileAudioTests` enforces all of it.
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
