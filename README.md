# 🧬 Garmin Bio-Intelligence Terminal

An automated, end-to-end encrypted physiological dashboard and intelligence pipeline for athlete Harvin ([@Zurplox](https://github.com/Zurplox)), continuously syncing biometrics from Garmin Connect™ and computing deep biological baselines.

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

Every Garmin endpoint can fail independently. A failed call used to be swallowed and a hard-coded placeholder substituted, then rendered indistinguishably from a measurement. The pipeline now records the origin of every metric group and publishes it:

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

Values that are *models* rather than measurements are labelled as such: **Day Strain** is computed from steps, session duration × aerobic training effect, and all-day stress; it is not a Garmin field.

---

## 📊 Tracked metrics

| Metric | Source | Notes |
|:---|:---|:---|
| Sleep architecture (186 nights) | Garmin | total, deep, REM, light, awake, sleep stress, nocturnal respiration |
| Overnight HRV + 7d rolling baseline | Garmin | personal corridor derived from Garmin's own balanced range |
| Resting heart rate (1,506 days, 2022→now) | Garmin | daily, 30/180-day and per-year averages, all-time baseline |
| Body Battery charge/drain | Garmin | falls back to a flagged placeholder if unavailable |
| 24-hour stress distribution | Garmin stress samples | bucketed 0–25 / 26–50 / 51–75 / 76–100, excludes sentinel values, percentages always total 100 |
| Circadian architecture | derived from sleep onsets | median onset, night-to-night spread, melatonin gate, alignment % |
| Sleep need & bedtime | derived | baseline 7h30m + debt paydown + strain need, rendered as a 12-hour clock |
| Day strain, readiness, injury risk | derived | thresholds in `bio_policy.py`, models in `bio_analytics.py` |
| Recovery score, readiness, bands | derived by the rule engine | always computed from the measurements above; the optional model cannot change them |
| AI clinical narrative | Gemini (optional) | prose only; the UI discloses which engine wrote the words |
| Workout feed | Garmin | normalised into Running / Walking / Cycling / Gym |

---

## 🧱 Code structure

Six Python modules and one static page, split by concern — see [ARCHITECTURE.md](ARCHITECTURE.md) for the ownership rules.

| Module | Owns |
|:---|:---|
| `bio_policy.py` | every threshold, band, tone and metric label — the single source of meaning, mirrored into the payload so the browser compares nothing itself |
| `bio_analytics.py` | all arithmetic: baselines, series windows, strain, sleep need, readiness, injury risk |
| `provenance.py` | live-vs-fallback recording and the publish gate |
| `garmin_source.py` | Garmin authentication and endpoint access (the only module that talks to Garmin) |
| `clinical_engine.py` | the deterministic rule engine (sole author of scores, bands, tones and risk) plus the optional Gemini narrative overlay |
| `sync.py` | the run: fetch → analyse → publish |
| `index.html` | rendering and interaction only |

---

## 🤖 Score ownership and the optional model

One owner per fact: the **deterministic rule engine computes every published number** — recovery score, its band, tone and zone, readiness, injury risk, the illness risk level and the training target — from the measurements in this run. Gemini, when `GEMINI_API_KEY` is set, writes **prose only**: the three analysis paragraphs and the narrative directives. Its own recovery score is recorded as `model_score` for comparison and never published.

This is not cosmetic. Publishing the model's number meant the same physiology produced **different verdicts depending on which engine answered**: one day's byte-identical inputs published recovery 94% / GREEN / PRIME with no key and 62% / YELLOW / READY with one — a 32-point swing that flipped the training advice. The payload carries `score_source: "deterministic"` and `narrative_source`, so a reader can always tell which engine produced which half. Bands are still recomputed from the score rather than trusted from the model, which is what stops the old "score 68 labelled YELLOW" contradiction from returning.

---

## ⚙️ Automated pipeline (GitHub Actions)

`daily_sync.yml` runs at **08:00 AM SGT (00:00 UTC)** with an **08:30 SGT** catch-up, serialised by a concurrency group so the two runs cannot race to push:

1. Authenticates headlessly with Garmin SSO using encrypted session tokens in `GARMIN_TOKENS`.
2. Ingests sleep architecture, HRV, multi-year RHR, Body Battery, stress telemetry and activities.
3. Records data provenance and aborts if the core metrics are unavailable.
4. Scores clinical intelligence with the rule engine, then optionally overlays Gemini's narrative.
5. Encrypts with AES-256-GCM and commits the vault.

`tests.yml` runs on every push and pull request: the offline unit suite, a frontend syntax check, and a Web Crypto decryption of the vault.

---

## 🔄 Manual refresh

The dashboard is a static page, so it cannot run the Python pipeline itself. The **Refresh** button therefore does the two things it honestly can:

1. **Re-read the published vault** (always available, no configuration). It fetches `data/biometrics.enc.json` with a cache-busting request, compares `data/status.json` against the publish this session already loaded, and re-decrypts and re-renders when there is something new. If nothing has changed it says so instead of pretending to work.
2. **Trigger a Garmin sync** (opt-in). The gear button beside it accepts a GitHub token with `Actions: read and write` on this repository. With a token stored, Refresh POSTs a `workflow_dispatch` to `daily_sync.yml`, then polls `status.json` for up to 15 minutes — a full sync takes several minutes and Pages has to redeploy — and loads the new vault automatically when it lands. Without a token, the same button only re-reads the vault, and says so.

The token is kept in this browser's local storage, is never committed, and is sent nowhere except `api.github.com`. It is optional because a static page has no other way to authenticate as you; if you would rather not store one, trigger the sync from the Actions tab and press Refresh afterwards.

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
python -m unittest discover -s tests -t . -v      # 60 tests
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
| `M` | Toggle micro-audio feedback |
| `P` | Print / export clinical PDF |
| `R` | Refresh: re-read the published vault (and trigger the sync if a GitHub token is stored) |
| `L` | Lock the vault and purge decrypted data (destroys chart instances, not just the global) |
| `ESC` | Dismiss modals and floating callouts |

---

## 📝 Correctness & transparency pass — 2026-09-21

* **Timezone-correct sync times.** Timestamps were naive, so browsers read a UTC sync time as local; the dashboard now emits and renders `+00:00` values explicitly in SGT.
* **12-hour clock fixed.** The vault contained `"22:03 PM SGT"` and `"22:15 PM — 22:45 PM SGT"`; both are now proper 12-hour clocks.
* **Real stress telemetry.** The 24-hour distribution was inferred from a single daily average with hard-coded percentages and labelled as 24-hour architecture. It is now computed from Garmin's ~3-minute stress samples (91% rest / 9% low on the first live run, versus the invented 75/18/5/2).
* **Real circadian metrics.** `88%` alignment and a fixed melatonin window were hard-coded; both now come from the athlete's own sleep onsets (median 11:57 PM, 55-minute spread, 73% alignment on the first live run).
* **Day Strain recalibrated.** The old formula clamped at a hard floor so every light day reported an identical `3.8`; the model is now monotonic, unbounded at the bottom, and documented.
* **Dead UI values removed.** The Body Battery graphic was frozen at `78%` regardless of data, the stress narrative always said `75%`, the recovery-factor/drain-rate labels and the "Immune Status: Highly Resilient" line were static, and the sleep/HRV night counts were literal strings.
* **Scrubber accuracy.** Pointer positions were mapped as a fraction of the whole canvas, ignoring the 64px axis gutter; on a 30-day HRV window that drifted up to 5 days. Scrubbing now resolves through the chart's own scale and anchors callouts to the data point.
* **Real lock semantics.** Logging out destroys chart instances and clears rendered tables instead of only nulling a global.
* **KDF work factor raised** to 600,000 iterations, with the cost read from the envelope rather than hard-coded at `100000`.
* **Baseline windowing hardened.** "Recent N days" windows now sort by date instead of trusting endpoint ordering.
* **Escaping + accessibility.** Garmin activity names and AI directives are HTML-escaped; the 13 disclosure toggles use a shared `toggleInfo()` helper with `aria-expanded`.

---

## 🗂️ Architecture pass — 2026-09-21

* **One owner per concern.** `sync.py` (1,363 lines mixing HTTP, analytics, model validation and display formatting) became four modules: `garmin_source.py`, `bio_analytics.py`, `clinical_engine.py` and a 250-line orchestrator. `verify_garmin.py` and `encrypt_data.py` were already separate and were left alone.
* **One owner per number.** Thresholds, bands and tones moved into `bio_policy.py`; the recovery zone alone existed in seven places and had already shipped a self-contradiction.
* **The browser stopped computing scores.** Readiness and injury risk were re-derived in the render loop from a second copy of every threshold. The engine now computes them and the payload carries the band, tone, badge, briefing text and the four contributing factors, which the injury card renders instead of its old static markup.
* **Ordering is decided where the windows are.** Positional windows ("the last 30 days") are sliced inside `bio_analytics`, and `latest_record()` replaces `list[-1]` assumptions in the orchestrator.
* **Prose no longer asserts unmeasured numbers.** The rule engine's verdicts were rebuilt from measured values: the hard-coded "Peak 5-minute HRV reached 89 ms", the fixed "+4.3 years younger" advantage, a literal 22:15 wind-down, and the model's own sleep-stress wording were all removed.
* **Visible defect fixed:** the monthly RHR callout rendered `2026-09 (undefined)` because the payload never carried a `year` field.

---

*Built with Python, Garmin Connect API, Web Crypto API, Tailwind CSS, and Chart.js.*
