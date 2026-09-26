# 🧬 Meridian Bio-Intelligence Terminal

An automated, end-to-end encrypted physiological dashboard and intelligence pipeline for athlete Harvin ([@Zurplox](https://github.com/Zurplox)), continuously syncing biometrics from the linked device account and computing deep biological baselines.

* **Live:** https://zurplox.github.io/garmin-bio-dashboard/
* **Stack:** Python ingestion → AES-256-GCM vault → single-file static dashboard (Tailwind + Chart.js, no build step)

---

## 🔒 Encryption: what it does and does not protect

The vault (`data/biometrics.enc.json`) is encrypted with **AES-256-GCM** using a key derived with **PBKDF2-HMAC-SHA256 (600,000 iterations, OWASP current guidance)**. The browser derives the key locally with the W3C Web Crypto API and decrypts in memory only. The envelope records the work factor it was written with, and the client honours that field, so the cost can be raised later without invalidating existing ciphertext.

It is important to be precise about the threat model, because the encryption here is **not** confidentiality against anyone who reads this repository:

| Property | Status |
|:---|:---|
| No plaintext health data committed | ✅ `data/biometrics.json` is gitignored; only ciphertext ships |
| Tamper-evident | ✅ AES-GCM authentication rejects any modified ciphertext |
| Stops casual scraping of a public URL | ✅ the raw JSON is not human-readable |
| Confidential against someone who reads this repo | ❌ **the viewer passphrase is a fallback default in `encrypt_data.py`** |

Because `DEFAULT_PASS` exists in a public repository and is used whenever the `GARMIN_DASHBOARD_PASS` secret is unset, anyone who reads this source can decrypt the published vault. To get real confidentiality:

1. Set `GARMIN_DASHBOARD_PASS` as a repository secret (the workflow already prefers it).
2. Remove `DEFAULT_PASS` from `encrypt_data.py` so a missing secret fails loudly instead of silently falling back.
3. **Rotate the passphrase** — the current one is in the git history, so it must be changed, not just hidden.

**Master Lock** (`Barabara` tier) is a client-side convenience lock: it stores a flag in `localStorage`, purges in-memory data, and destroys chart instances. It is per-browser, not a server-side access control, and `data/status.json` is overwritten by every sync.

---

## 📈 Data provenance: which numbers are real

Every device endpoint can fail independently. A failed call used to be swallowed and a hard-coded placeholder substituted, then rendered indistinguishably from a measurement. The pipeline now records the origin of every metric group and publishes it:

```json
"data_quality": {
  "metrics": { "sleep": { "source": "live", "note": "186 nights of sleep architecture" }, ... },
  "degraded": [],
  "live_count": 10,
  "total_count": 10,
  "live_pct": 100,
  "core_degraded": []
}
```

In the UI this becomes:

* a header chip reading `10/10 LIVE` or `N ESTIMATED`, which opens a per-metric breakdown;
* an `est.` marker on any individual value that is a placeholder or model output;
* a `STALE DATA` flag when the last successful sync is more than 30 hours old;
* a hard abort (`exit 1`) when any **core** metric (sleep, RHR, HRV) falls back — a run that would publish mostly placeholder physiology fails the workflow instead, leaving the previous vault in place.

Values that are *models* rather than measurements are labelled as such: **Day Strain** is computed from steps, session duration × aerobic training effect, and all-day stress; it is not a device field. Equally, a value the device did not supply is published as `null` and rendered `--`: the pipeline no longer substitutes a plausible-looking placeholder for a missing measurement, and the narrative is written from the account's own profile rather than from assumed facts about the athlete's life.

---

## 📊 Tracked metrics

| Metric | Source | Notes |
|:---|:---|:---|
| Sleep architecture (186 nights) | device | total, deep, REM, light, awake, sleep stress, nocturnal respiration |
| Overnight HRV + 7d rolling baseline | Garmin | personal corridor derived from the watch's own balanced range |
| Resting heart rate (1,506 days, 2022→now) | device | daily, 30/180-day and per-year averages, all-time baseline |
| Body Battery charge/drain | device | falls back to a flagged placeholder if unavailable |
| 24-hour stress distribution | device stress samples | bucketed 0–25 / 26–50 / 51–75 / 76–100, excludes sentinel values, percentages always total 100 |
| Circadian architecture | derived from sleep onsets | median onset, night-to-night spread, melatonin gate, alignment % |
| Sleep need & bedtime | derived | baseline 7h30m + debt paydown + strain need, rendered as a 12-hour clock |
| Day strain, readiness, injury risk | derived | thresholds in `bio_policy.py`, models in `bio_analytics.py` |
| Recovery score, readiness, bands | derived by the rule engine | always computed from the measurements above; the optional model cannot change them |
| AI clinical narrative | Gemini (optional) | prose only; the UI discloses which engine wrote the words, and the rule engine always publishes the everyday-words explanation as the paragraph beneath it |
| Workout feed | device | normalised into Running / Walking / Cycling / Gym |
| Blood oxygen (SpO₂) | device Pulse Ox | sparse by nature — the card publishes `days_recorded` out of the 120-day window beside the reading, because an on-demand sensor is not a nightly average |
| Location & travel | device activity records | the most-logged location is "home"; anywhere else is listed as travel with sessions and dates. No location is assumed |
| Session climate | device weather station | temperature, humidity, feels-like and dew point for the most recent session, converted from the account's own units |
| Heat & altitude acclimation | device max-metrics | the device's own estimate of heat adaptation, banded in `bio_policy` |
| Hydration | device hydration | daily target, logged intake and the latest session's measured sweat loss |
| Capacity (VO₂max, BMI, threshold HR) | account profile + max-metrics | age, sex, height and weight are read from the profile; BMI is computed from those, never assumed |
| Race forecasts | device race predictions | 5K / 10K / half / marathon, formatted from the device's own seconds |
| Intensity minutes, steps, floors, calories | device daily summaries | weekly moderate-equivalent minutes against the 150-minute guideline |
| Correlations | computed from the athlete's own paired days | curated pairs only, each published with `r`, the paired-day count and a plain-English note; a pair below 14 paired days or |r| < 0.35 is refused rather than published |

---

## 🧑‍🏫 Coaching, not just monitoring

The dashboard reports what your numbers say; the **Coach** section says what to do
about them. Each training domain the device records gets one card — Recovery &
Load, Strength & Muscle, Running & Hard Cardio, Walking & Daily Movement, Hiking &
Hills, Sleep — and every card carries the reading, a plain-English restatement, one
concrete action, how it progresses, the condition that cancels it, and the study the
rule came from.

Two rules keep it honest. A domain with no measured history publishes **no
prescription** (it says NOT MEASURED and offers to start from your first logged
session), and a personal pattern is withheld until it has enough paired days. The
"Know Yourself" panel compares your hard days with your quiet ones rather than
reporting one side, and labels every finding an association, not a cause.

The rules are drawn from published work — load ratios (Gabbett 2016), HRV-guided
prescription (Vesterinen 2016), sleep extension (Mah 2011) and sleep regularity
(Windred 2024), resistance-training frequency (Schoenfeld 2016), daily steps
(Paluch 2022), heat-acclimation decay (Daanen 2018), grade cost (Minetti 2002),
fluid replacement (ACSM 2007) and the WHO activity floor. Each anchor publishes what
the study found **and what it does not settle**, so a recommendation can be argued
with rather than merely obeyed.

## 🧭 Today at a glance

The first card on the page is three readings and nothing else: **today's steps**
against the goal your device holds (with yesterday's own total underneath), **what
was trained in the last 24 hours** (one pip per session, the minutes and the
activity types, and how long ago the last one finished), and **last night** against
your own sleep need. Every value is resolved by the engine and published in the
`today_summary` group, the 24-hour window is labelled with the moment it ends so
"the last 24 hours" is never ambiguous, and a reading that was not measured reads
`--` like every other absent number on the page.

## 🎛️ Instruments you can point at

The **Load, Movement & Consistency** section turns three readings into things you can
interrogate with a pointer or the Tab key.

- **Workload balance dial** — the four ACWR bands, drawn on the exact ratios that
  separate them, with the needle on today's ratio. Hovering or focusing an arc reads
  its range and policy's own plain-English meaning for it. The edges, the tones and
  the wording all arrive in the payload, so the arc you can see is the band the badge
  and the coach are reading.
- **Today's movement ring** — today's steps against the goal your own device set,
  with floors, active calories and this week's intensity minutes beside it. A day with
  no step count yet reads `--`, because "not measured" and "measured nothing" are
  different claims.
- **Training consistency** — one cell per day for the last 120, filled by the minutes
  the device logged. Hover or tab to a day to read its date, session count and
  activity types; a blank cell means no session was logged, not that the day was bad
  for you.

## 🧱 Code structure

Seven Python modules and one static page, split by concern — see [ARCHITECTURE.md](ARCHITECTURE.md) for the ownership rules.

| Module | Owns |
|:---|:---|
| `bio_policy.py` | every threshold, band, tone and metric label — the single source of meaning, mirrored into the payload so the browser compares nothing itself |
| `bio_analytics.py` | all arithmetic: baselines, series windows, strain, sleep need, readiness, injury risk |
| `provenance.py` | live-vs-fallback recording and the publish gate |
| `garmin_source.py` | Garmin authentication and endpoint access (the only module that talks to Garmin) |
| `bio_correlate.py` | patterns between the athlete's own daily channels, and the home/away comparison |
| `bio_coach.py` | prescriptions per training domain and the personal patterns, from measured sessions, steps, nights and readings |
| `clinical_engine.py` | the deterministic rule engine (sole author of scores, bands, tones and risk) plus the optional Gemini narrative overlay |
| `sync.py` | the run: fetch → analyse → publish |
| `index.html` | rendering and interaction only |

---

## 🤖 Score ownership and the optional model

One owner per fact: the **deterministic rule engine computes every published number** — recovery score, its band, tone and zone, readiness, injury risk, the illness risk level and the training target — from the measurements in this run. Gemini, when `GEMINI_API_KEY` is set, writes **prose only**: the three analysis paragraphs and the narrative directives. Its own recovery score is recorded as `model_score` for comparison and never published. The API call uses an ordered remote-model fallback chain: `gemini-3.8-flash` → `gemini-3.7-flash` → `gemini-3.6-flash` → `gemini-3.5-flash`; set `GEMINI_MODELS` to a comma-separated list if Google changes availability. No model package is installed locally.

Every analysis paragraph ends with a **plain-English sentence**, and the rules write that sentence whichever engine wrote the paragraph above it. `clinical_engine` pairs each verdict with its restatement (`PLAIN_ENGLISH_PREFIX`), and `synthesize()` appends it after the prose, so a reader who does not know the clinical vocabulary still gets the same takeaway — and a model paragraph can never drop it.

This is not cosmetic. Publishing the model's number meant the same physiology produced **different verdicts depending on which engine answered**: one day's byte-identical inputs published recovery 94% / GREEN / PRIME with no key and 62% / YELLOW / READY with one — a 32-point swing that flipped the training advice. The payload carries `score_source: "deterministic"` and `narrative_source`, so a reader can always tell which engine produced which half. Bands are still recomputed from the score rather than trusted from the model, which is what stops the old "score 68 labelled YELLOW" contradiction from returning.

---

## ⚙️ Automated pipeline (GitHub Actions)

`daily_sync.yml` runs at **08:00 AM SGT (00:00 UTC)** with an **08:30 SGT** catch-up, serialised by a concurrency group so the two runs cannot race to push:

1. Authenticates headlessly with the device account's SSO using encrypted session tokens in `GARMIN_TOKENS`.
2. Ingests sleep architecture, HRV, multi-year RHR, Body Battery, stress telemetry and activities.
3. Records data provenance and aborts if the core metrics are unavailable.
4. Scores clinical intelligence with the rule engine, then optionally overlays Gemini's narrative.
5. Encrypts with AES-256-GCM and commits the vault.

`tests.yml` runs on every push and pull request: the offline unit suite, a frontend syntax check, and a Web Crypto decryption of the vault.

`backup_build.yml` runs on every push to `main` — and again whenever the daily sync finishes, because GitHub does not start workflows from a push made with the default `GITHUB_TOKEN`, so the data commit would otherwise leave it behind — and attaches a zip of the build to the persistent **[build-backups](../../releases/tag/build-backups)** release — `meridian-build-latest.zip` always the newest, a timestamped copy beside it kept as a point-in-time rollback (the newest ten are retained). It is a release asset rather than a committed file on purpose: release storage survives a force-pushed or lost branch, which is exactly the failure a rollback copy exists to cover, and a committed zip would grow the repository for ever.

---

## 🔄 Manual refresh

The dashboard is a static page, so it cannot run the Python pipeline itself. The **Refresh** button therefore does the two things it honestly can:

1. **Re-read the published vault** (always available, no configuration). It fetches `data/biometrics.enc.json` with a cache-busting request, compares `data/status.json` against the publish this session already loaded, and re-decrypts and re-renders when there is something new. If nothing has changed it says so instead of pretending to work.
2. **Trigger a sync**. Three routes, tried in this order:

   * **The embedded credential** (current default, owner-approved 2026-09-26). The page ships with the sync credential embedded as two Base64 fragments that reassemble at runtime, so a press works on the public site with nothing configured on any device. This is not a secrecy measure and is not one by accident: the repository is public, so anyone who reads the source can reassemble the value and start a run — the 5-minute run guard is the only brake, and the owner accepts that trade. GitHub's push protection decodes Base64 and rejects a whole encoded credential on push (and any alert on a literal could auto-revoke the real token), which is why it travels as fragments; the guard test still fails on any literal credential shape.
   * **A relay** (`relay/`, deploy it once) holds the GitHub token as its own encrypted secret, so the page never sees a credential and no device has to be keyed in. Refresh POSTs to the relay, which starts `daily_sync.yml` on `main` — the workflow file and the ref are pinned there, only this dashboard's origin may call it, and a second request inside its window is refused. With `SYNC_RELAY_URL` set, nothing has to be configured on any device, and the embedded credential stops being used. This remains the right long-term fix.
   * **A token in this browser** is the fallback for a checkout with no relay. There is no token field in the page: open the dashboard once with the token in the URL fragment and it is stored, then stripped from the address bar and history:

     ```
     https://zurplox.github.io/garmin-bio-dashboard/#token=github_pat_...
     ```

     A fragment is never sent to a server, so the token stays in the browser; it is kept in local storage, never committed, and sent nowhere except `api.github.com`. `#token=` with nothing after it forgets the stored token, and a value that is not a token is refused rather than stored. A token needs `Actions: read and write` on this repository only.

   Either way Refresh then polls `status.json` for up to 15 minutes — a full sync takes several minutes and Pages has to redeploy — and loads the new vault automatically when it lands.

   A press reaches GitHub **first** when a trigger is available: the dispatch is sent before any "already up to date" check, so the button can never read as dead against a fresh vault. Only when no dispatch happened (no trigger configured, or one was sent inside the 5-minute dedupe window) does the short-circuit compare `status.json` and report "already up to date" without a fetch.

Why a token was not simply embedded in the page: this repository is public and has secret scanning and push protection enabled, so GitHub detects and revokes a leaked token — it would work for minutes and then stop. Every visitor would also have been able to start runs. On 2026-09-26 the owner weighed exactly that and approved the embedded-credential route anyway (encoded, per above, so push protection does not auto-revoke it); the relay remains the design that removes both problems and is the recommended migration.

The `R` key does the same thing as the button.

### One press, one action

Repeat clicks are refused rather than queued, so neither the vault nor GitHub can be hammered:

| Guard | Behaviour |
|:---|:---|
| While running | The button is disabled, the icon spins and the label reads `Refreshing…`. A second press (button or shortcut) is refused with a message, and only one request is ever in flight. |
| Cooldown | For 60 seconds after every attempt the button stays disabled and counts down (`Wait 42s`), then returns to `Refresh`. Pressing during it is refused with the seconds remaining. |
| Workflow dispatch | At most one `workflow_dispatch` per 5 minutes, remembered across page reloads. Inside that window Refresh only re-reads the published vault and says so. |
| Locking mid-run | The poll stops, the cooldown is cleared and the button returns to `Refresh`, so unlocking never leaves a dead button — and nothing keeps polling behind the lock screen. |

Every attempt ends with a notification: what it did, or exactly why it did nothing.

---

## 🧪 Tests

No network, no credentials required.

```bash
python -m unittest discover -s tests -t . -v      # 251 tests
node tests/check_frontend_syntax.mjs index.html   # single-file frontend has no build step
node tests/verify_vault_webcrypto.mjs             # decrypts the vault through the browser crypto path
```

Covered: 12-hour clock formatting, evening-anchored sleep-onset parsing, stress bucketing and sentinel handling, percentage rounding, baseline and window ordering against out-of-order input, circadian alignment, the strain model's monotonicity and bounds, sleep-need arithmetic, the readiness/injury ladders, model-output validation, payload assembly with its publish gate, provenance reporting, and the encryption envelope.

---

## 🖥️ Running locally

```bash
python verify_garmin.py     # one-off: create ~/.garminconnect/garmin_tokens.json
python sync.py              # writes data/biometrics.json (gitignored)
GARMIN_PBKDF2_ITERATIONS=600000 python encrypt_data.py
python -m http.server 8971 --bind 127.0.0.1   # then open http://127.0.0.1:8971
```

`data/biometrics.json` is gitignored and must stay that way — it is the only plaintext copy of the dataset.

---

## ⌨️ Dashboard hotkeys

| Key | Action |
|:---:|:---|
| `1` – `9` | Jump to a dashboard section |
| `O` | Toggle the RHR dual-axis overlay |
| `M` | Toggle tactile feedback — the chirps and the vibration (on for a first visit; a refusal is remembered) |
| `F` | Fill the screen / leave it (hidden where the browser has no fullscreen API) |
| `P` | Print / export clinical PDF |
| `R` | Refresh: re-read the published vault (and trigger the sync if a GitHub token is stored) |
| `L` | Lock the vault and purge decrypted data (destroys chart instances, not just the global) |
| `ESC` | Dismiss modals and floating callouts |

---

## 📝 Correctness & transparency pass — 2026-09-21

* **Timezone-correct sync times.** Timestamps were naive, so browsers read a UTC sync time as local; the dashboard now emits and renders `+00:00` values explicitly in SGT.
* **12-hour clock fixed.** The vault contained `"22:03 PM SGT"` and `"22:15 PM — 22:45 PM SGT"`; both are now proper 12-hour clocks.
* **Real stress telemetry.** The 24-hour distribution was inferred from a single daily average with hard-coded percentages and labelled as 24-hour architecture. It is now computed from the device's ~3-minute stress samples (91% rest / 9% low on the first live run, versus the invented 75/18/5/2).
* **Real circadian metrics.** `88%` alignment and a fixed melatonin window were hard-coded; both now come from the athlete's own sleep onsets (median 11:57 PM, 55-minute spread, 73% alignment on the first live run).
* **Day Strain recalibrated.** The old formula clamped at a hard floor so every light day reported an identical `3.8`; the model is now monotonic, unbounded at the bottom, and documented.
* **Dead UI values removed.** The Body Battery graphic was frozen at `78%` regardless of data, the stress narrative always said `75%`, the recovery-factor/drain-rate labels and the "Immune Status: Highly Resilient" line were static, and the sleep/HRV night counts were literal strings.
* **Scrubber accuracy.** Pointer positions were mapped as a fraction of the whole canvas, ignoring the 64px axis gutter; on a 30-day HRV window that drifted up to 5 days. Scrubbing now resolves through the chart's own scale and anchors callouts to the data point.
* **Real lock semantics.** Logging out destroys chart instances and clears rendered tables instead of only nulling a global.
* **KDF work factor raised** to 600,000 iterations, with the cost read from the envelope rather than hard-coded at `100000`.
* **Baseline windowing hardened.** "Recent N days" windows now sort by date instead of trusting endpoint ordering.
* **Escaping + accessibility.** Device activity names and AI directives are HTML-escaped; the 13 disclosure toggles use a shared `toggleInfo()` helper with `aria-expanded`.

---

## 🗂️ Architecture pass — 2026-09-21

* **One owner per concern.** `sync.py` (1,363 lines mixing HTTP, analytics, model validation and display formatting) became four modules: `garmin_source.py`, `bio_analytics.py`, `clinical_engine.py` and a 250-line orchestrator. `verify_garmin.py` and `encrypt_data.py` were already separate and were left alone.
* **One owner per number.** Thresholds, bands and tones moved into `bio_policy.py`; the recovery zone alone existed in seven places and had already shipped a self-contradiction.
* **The browser stopped computing scores.** Readiness and injury risk were re-derived in the render loop from a second copy of every threshold. The engine now computes them and the payload carries the band, tone, badge, briefing text and the four contributing factors, which the injury card renders instead of its old static markup.
* **Ordering is decided where the windows are.** Positional windows ("the last 30 days") are sliced inside `bio_analytics`, and `latest_record()` replaces `list[-1]` assumptions in the orchestrator.
* **Prose no longer asserts unmeasured numbers.** The rule engine's verdicts were rebuilt from measured values: the hard-coded "Peak 5-minute HRV reached 89 ms", the fixed "+4.3 years younger" advantage, a literal 22:15 wind-down, and the model's own sleep-stress wording were all removed.
* **Visible defect fixed:** the monthly RHR callout rendered `2026-09 (undefined)` because the payload never carried a `year` field.

---

## 🗣️ Plain-language & motion pass — 2026-09-21

* **The explanation is now a paragraph, not a label.** Every analysis ships as the clinical paragraph followed by a rule-written explanation underneath it (`PLAIN_PARAGRAPH_SEPARATOR`), and the page renders the two with **no "In plain English:" prefix** anywhere in the UI. A reader who does not know the vocabulary meets the plain version automatically, and a model still cannot drop it.
* **Written for a person, not a clinician.** First mention carries the long form in brackets (*Overnight HRV (Heart Rate Variability)*); each analysis card carries a one-line subtitle saying what it is about; the quadrant chart explains what resting heart rate and heart rate variability mean and why the two are read together; the quadrant callout now shows the scientific description *and* an everyday sentence.
* **Today leads the quadrant chart.** The legend sorts *Today (Latest)* first, its node is drawn larger, and the note under the chart opens with tonight's coordinates before it explains the axes — previously today was the last legend entry and the reading was buried in the text.
* **The page moves when you reach it.** `playVisuals()` replays every growing visual the first time it hits the fold: dials close, the movement ring draws itself, the battery refuels, the stress and injury bars fill, and every chart plots its points on arrival instead of animating off-screen. A scroll-position sweep is used rather than an intersection threshold, because a fast flick could otherwise skip a gauge and leave it showing an empty dial — a value that was never measured.
* **Motion with weight.** Timings are slow and spring-eased (2.1 s dials and rings, 1.6 s charts) with a small overshoot, so a gauge bounces into its value the way a physical needle settles rather than snapping. Bars in the stress, injury and load panels fill one after another (`primeGrow`'s stagger), the sleep chart grows night by night (`animation.delay` keyed on the data index), the stage ring sweeps in, the training heat map cascades day by day, and the workload dial springs as it lands. Headline numbers count up through `primeCount`, which refuses to write an interpolated frame over a value a fresh render already published.
* **The enlarged heat map is square.** The ⛶ focus view sizes itself from `vmin`, so the panel stays a square instead of a stretched strip and each day square stays square inside it — the compact timeline above it is unchanged.
* **Nothing is recomputed for motion.** Each element is already drawn at its real value; the layer only re-applies it. `prefers-reduced-motion` skips the whole layer, cards are never briefly invisible, and the hidden start state only exists once the script has marked the page motion-live.
* **iOS-style touch feedback.** Cards and sections rise into place with a staggered ease, and every button presses in and springs back.

---

## 📱 Final build items — 2026-09-22

* **Fullscreen.** A draw-it-out / draw-it-in button in the header asks the browser for the whole
  screen, and the icon, the spoken label (`aria-label`) and the tooltip all follow the browser's
  own state. Where the API does not exist — an iPhone browser has none — the button is never
  revealed, and `F` says so with a refusal cue rather than a control that can do nothing. Both
  spellings are used (`requestFullscreen` and `webkitRequestFullscreen`, plus `fullscreenEnabled`,
  because a frame can have the method and still not be allowed to use it).
* **Haptics, with the sound as the fallback.** Every cue in the vocabulary now has a vibration
  pattern beside it, so a cue is one event in two senses; a phone buzzes, a desktop and an iPhone
  simply hear it and nothing stands in for the buzz. One switch controls both — the header button
  reads **Feedback ON/OFF** — and a pattern is a tap or a tap-pause-tap, never over 160 ms, so a
  cue is felt as feedback rather than as an alarm going off in a pocket.
* **Feedback you can hear.** The first pass was quiet enough to miss on a laptop across the room, so
  every chirp is lifted by one gain (`MICRO_AUDIO_GAIN = 1.6`) and held under one ceiling
  (`MICRO_AUDIO_CEILING = 0.04`). The table keeps its relative volumes; the policy lives in the one
  chirp primitive, so the pitch ladders are lifted with everything else.
* **No emoji in the interface.** The explanation panels keyed their bands with coloured circles,
  the status chip with a tick or a warning sign, the metric cards with a gene, a heart, a
  stethoscope and a bolt, and the greeting with a waving hand. All of it is drawn now: the bands are
  coloured dots in the page's own language, the status chip is a filled or hollow mark, the header
  controls are stroked SVG icons, and the lock screen's messages are plain sentences. Two marks
  remain on purpose and sit outside the emoji ranges — the information mark that opens a dropdown,
  and the filled circle that stands for a band, a state or a quadrant. A test keeps it that way.
* **Every target line states its own number.** The coach cards' dashed goal line (8,000 steps in the
  walking card) was only explained in the caption underneath it; the number now sits on the line
  itself, at its right-hand end, on whichever side of the line has room.

---

## 🎚️ Motion reaches the reader — 2026-09-22

* **A fill plays when you reach it, not four seconds after the paint.** The filler used to
  publish a bar's value *and* play its spring on the same four-second failsafe, so every
  bar, gauge and battery below the fold had already filled and settled while the reader was
  still at the top of a 16,000-pixel page. The two are separate promises now: the value is
  published on the timer either way, because a measured bar drawn as nothing is the one
  outcome that is never allowed, and the spring is played only by the reveal. Measured on
  the running page: at 6.5 s the battery carries no spring at all, and scrolling to it plays
  the fill (scale 0.70 → 1.05 → 1.0) while its label rolls 0 → 53%.
* **The battery visibly refuels.** The height transition that raced the spring is gone, a
  highlight sweeps up the cell on the fill's own beat, and the level reads as a number that
  climbs with it — instead of a card fading in around a bar that was already full.
* **A hover never moves the page.** Every chart inspector reserves the tallest state it can be
  in at the current width (`reserveHoverPanel`), so a longer quadrant label and sentence — or a
  reading that carries its overlaid resting heart rate, or a month whose deviation string is
  wider — cannot wrap to another line and shift the chart out from under the cursor, which used
  to land the cursor on a different reading and move the chart again. The sentence-len panels
  measure each state they can be in; the readings panels reserve the one state where every field
  is at its widest, which bounds every reading they can be pointed at. Measured: 45 pointer
  steps across each of the four charts held one canvas position, one panel height, one scroll
  position and an unchanged document height (the scatter's reservation follows the window both
  ways — 380 px reserves 218 px, 1000 px reserves 131 px).
* **Every animation has its own voice.** `grow` for a bar or the battery filling, `ring` for
  a gauge or ring closing, `count` for a headline number, `chart` for a canvas settling,
  `stagger` for a panel assembling, `tick` for the inspector moving to another night, and
  `reveal` for the heat-map cascade. Repeats of a *motion* cue inside its own window merge
  into the first, because one panel filling is one movement rather than a drum roll; a press
  still answers every time it is made.
* **The map and its legend agree.** The legend chip for the corner the inspected night sits in
  lights up in that band's own colour, and the inspector's readings settle onto each new night
  instead of swapping between frames.
* **Tonight's diamond announces itself.** When the quadrant map finishes coming forward, two rings
  leave the newest night and fade, with a cue of its own (`today`). They are placed at the pixel
  the chart measured for that reading and read out of the chart that drew it, so on a map of 187
  nights "where am I tonight" is answered at a glance and the rings sit on the dot rather than
  near it, at any window size.
* **The inspector arrives, and is the size of what it says.** A callout that is not already on
  screen rises into place and settles onto the node it describes rather than appearing — once,
  with one `hover` cue for all four charts — and it is placed from the box it actually renders
  instead of a fixed 230 × 175 (measured: the quadrant callout draws 340 × 186, so one opened near
  the top of the window used to be placed as if it were smaller than it is). It is a popover now
  too, not a block stretched to the width of the document.

---

## 🔍 Audit fixes — 2026-09-22

An independent audit of the shipped build measured three defects. Each was reproduced on the
running page before the fix and measured again after it, and each is now pinned by a test.

* **A shortcut is a bare key, never a browser chord.** The keyboard handler answered any key it
  recognised whatever was held with it, so `Ctrl+F` (the reader looking for a word on the page)
  opened the full screen, `Ctrl+P` printed a second time, and `Ctrl+R` — the reader reloading the
  page — called `refreshVault`, which starts a real GitHub sync in a browser that holds a relay URL
  or a token. Ctrl, Meta and Alt now return before the key is even read; Shift deliberately does
  not, because `Shift+R` is still a reader holding shift. Measured with every branch instrumented so
  nothing could fire: before, all six chords reached a branch (`Ctrl+f`, `Meta+f` and `Alt+f` each
  reached `toggleFullscreen`, `Ctrl+r` reached `refreshVault`); after, all six are recorded and
  **zero** branches run, bare `f`/`t`/`r` still work, and typing into the real passphrase field
  reaches nothing at all.
* **Tonight's beacon cannot widen the page.** The rings are scaled 3.6× and the chart's container
  did not clip them, so a ring left at a wide landing sat off the edge of the card and dragged the
  whole document sideways — a phone page that scrolls horizontally because of a mark that was never
  meant to be seen outside its chart. Measured on the pre-fix build at a 320 px viewport: rings
  attached gave `scrollWidth` 561 against `clientWidth` 277, and 348 with the rings detached. The
  container now clips (`overflow: hidden`), which changes where a ring is *seen* and never where it
  *lands* — the ring's position is still the pixel the chart measured for tonight's reading, checked
  equal at 320, 380, 768 and 1280 px — and never when it plays. After: the identical state reads
  352 px, the same as with the rings detached, and the same with the rings deliberately held at
  504 px on a 292 px viewport.
* **The no-emoji rule covers the model's words too.** It was enforced by a regex over `index.html`,
  which cannot see the briefing and coaching prose the payload carries, so one emoji from the model
  would have reached the explanation panels. Every vault payload now enters the page through one
  boundary — `payloadFromVault` decrypts and strips in that order — which walks the whole payload
  and removes only the pictogram ranges, so the page's own marks (the coloured dot, the information
  mark) are untouched by construction and a string carrying no emoji comes back byte for byte.
  Measured: a payload written with emoji in its analysis prose rendered through the page's own
  writer shows 6 emoji in the explanation panel before the boundary and **0** after it.

---

*Built with Python, the device data API, Web Crypto API, Tailwind CSS, and Chart.js.*
