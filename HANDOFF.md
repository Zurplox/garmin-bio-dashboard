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
  `week`, `window_days`, `pattern_caveat`, `available`.
* `correlations` — `findings`, `tested_pairs`, `skipped`, the floors
  (`min_days`, `min_r`), the window, and `location` (home/away from device logs).
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
   neutral tone. (One known exception remains, in the quadrant scatter's point
   data — see §8.)
3. **No static claim beside a live number.** A header pill that always says
   `LIVE`, a tier badge baked to `ATHLETIC`, a colour hardcoded next to a
   word — all three shipped once. Derive both from the payload.
4. **The model never owns a number.** Gemini may write prose; `score_source`
   stays `deterministic` and the model's number is recorded as `model_score`. This
   was proven both with and without a key, and on the runner.
5. **No credential in the repo.** `SecretGuardTests` scans sources, docs,
   workflows and tests for `ghp_`, `github_pat_`, `AIza`, private keys. The refresh
   token lives only in the viewer's browser `localStorage`
   (`garmin_github_token`). There is no way to make the button work without a
   per-device token, and that is deliberate: the repo is public.
6. **One press, one run.** The Refresh button is single-flight and greys out with
   a cooldown; it never fires two dispatches. Do not test it by spamming the
   workflow — the owner has asked repeatedly for no extra GitHub runs.
7. **Motion computes nothing.** `playVisuals()` reads what the render pass already
   wrote and re-applies it. Reduced-motion readers get final values, no hidden
   cards, no animation.

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

Since the last merge, `daily_sync.yml` has been dispatch-verified on `main`: the
runner fetches with the real key, publishes from the deterministic engine, commits
its data, and the published page loads the fresh vault.

---

## 8. Known open items (honest list)

* **The quadrant scatter still fabricates a word.** Its point data uses
  `data.today.hrv_status || 'BALANCED'`, the last place on the page where an
  absent field becomes a reassuring verdict. Every other surface was fixed in
  PR #6/#9; this chart was left deliberately out of scope. Small fix: the scatter's
  callout should read the published band/delta like the HUD does.
* **Population advice still exists in a few info panels** ("Elite endurance:
  60–100+ ms"). It is labelled as context, but the page's own rule is
  compare-to-your-own-baseline.
* **Blood oxygen coverage is low** (single-digit readings per window). The cards
  say so; nothing should be inferred from them yet.
* **Coaching domains can read NOT MEASURED** for weeks (running, hiking) when no
  session of that type exists in the window. That is intended, not a bug.
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
