# Architecture

Seven Python modules and one static page. Each concern has exactly one owner; if
you are about to write a threshold or a colour somewhere else, you are in the
wrong file. Personal facts have one owner too: the linked account's own profile,
read by `garmin_source.fetch_profile`. Anything the account did not supply ships
as `null` and renders as `--`, and the narrative is forbidden from assuming it.

```
                 garmin_source.py ──┐
   (the only device I/O)            │
                                    ▼
                            bio_analytics.py ──────► clinical_engine.py
                            (pure arithmetic)        (verdicts: rules; model narrates)
                                    │      │                 │
                                    │      └─ bio_correlate.py
                                    │         (channel relationships)
                                    └────────┬───────────────┘
                                             ▼
                                    sync.py (orchestrator)
                                             │
                          provenance.py ─────┤ publish gate
                          bio_policy.py ─────┘ policy snapshot + resolved bands
                                             ▼
                                  data/biometrics.json
                                             │ encrypt_data.py
                                             ▼
                                  data/biometrics.enc.json
                                             │ Web Crypto (in-browser)
                                             ▼
                                       index.html
```

## Ownership

| Concern | Owner | Rule |
|:---|:---|:---|
| Thresholds, bands, tones, metric labels, coaching targets, the study each coaching rule cites | `bio_policy.py` | Neither Python nor the browser may compare a metric against a number of its own; the payload carries resolved bands. `TONE_NAMES` is the whole colour vocabulary, and `COACH_PRIORITY` is the stated tie-break for which domain wins today. |
| Arithmetic (baselines, windows, strain, sleep need, readiness, injury risk) | `bio_analytics.py` | Pure functions: values in, values out. No I/O, no environment, no provenance. Also decides positional windows ("last 30 days") and which record is latest, and publishes an unmeasured overnight HRV or resting heart rate as `None` -- with no population tier for an absent pulse -- rather than a plausible default, so nothing downstream plots a night nobody measured. |
| Garmin access (auth, endpoints, parsing into records) | `garmin_source.py` | The only module that performs Garmin I/O. Records live/fallback provenance as it fetches. |
| Live/fallback bookkeeping and the publish decision | `provenance.py` | `DataQuality.publishable()` is the gate; the orchestrator consults it, nothing else decides. |
| Clinical synthesis | `clinical_engine.py` | The rule engine is the **only** author of the recovery score and of everything derived from it (band, tone, zone, readiness inputs, illness risk level, training target); Gemini is merged on top as narrative and its own score is kept as `model_score`, never published. Every analysis is published as **two paragraphs** -- the clinical one, then a rule-written explanation in everyday words -- joined by `PLAIN_PARAGRAPH_SEPARATOR` and appended by `synthesize()` after whichever engine wrote the clinical half. The page splits them and renders the explanation underneath with no label, so the reader always meets it and a model can never drop it. Band meanings in plain words live on the band itself in `bio_policy.py`. Returns meaning, never layout or colour classes. |
| Relationships between the athlete's daily channels | `bio_correlate.py` | Pure Pearson and comparison logic over series that were already measured. Curated pairs only, with a floor on paired days and coefficient magnitude, so nothing is published that its own thresholds call noise. Also decides home/away from device-logged locations. |
| Prescriptions — what to do about the numbers | `bio_coach.py` | Pure rules over already-measured sessions, steps, nights and readings. One card per training domain, each with the reading, the action, the progression, the guard-off and the studies behind it; a domain with no measured history publishes no prescription. Personal patterns need a floor on paired days, and they compare the hard side with the quiet side rather than reporting one of them. |
| The run itself (fetch → analyse → publish → report) | `sync.py` | Wires the modules, assembles the payload envelope (`updated_at`, `athlete`, `history`, `policy`), writes `data/biometrics.json`, prints the summary. |
| Payload encryption and status file | `encrypt_data.py` | Standalone; reads the pipeline's JSON, never imports the pipeline. |
| Rendering and interaction | `index.html` | Consumes resolved bands/tones from the payload. The only decisions it makes are layout, and the only colours are `TONE_STROKE` / `badgeClass()` fed by the engine's tones. Also owns presentation-only motion: `primeMotion(scope)` replays a gauge, ring, bar, count or chart the first time it reaches the fold, and it never computes a value -- it re-applies whatever the render pass already wrote. The first paint primes the document; a render that rebuilt a panel primes that panel again through `replayMotion(scope)`, so a re-render (filter, refresh, a re-opened enlarged map) animates exactly where it did on load. |

## Manual refresh

The refresh button touches nothing in the pipeline and writes nothing: it re-reads
the already-published vault (read-only, cache-busted) and, only when the viewer
has stored their own GitHub token, asks GitHub Actions to dispatch
`daily_sync.yml` and then polls `data/status.json` until a newer publish appears.
The passphrase is held in memory for the session (`sessionPassphrase`) purely so
a refresh can re-decrypt without re-prompting, and `purgeDecryptedData()` clears
it along with the data. Anything that would need the pipeline to run inside the
browser belongs on the workflow side instead.

## Data flow contract

`sync.build_payload()` emits, for the browser:

* `policy` — the tiny slice of policy the UI needs at render time (`day_strain.scale_max`, `freshness.stale_after_hours`), so staleness and gauge scaling are configured in one place;
* `today`, `baselines`, `history` — measurements, unchanged shape;
* `whoop`, `fitbit`, `garmin_signature` — derived blocks whose bands, badges and tones are already resolved;
* `readiness`, `injury_risk` — the composite scores, their bands/tones and the four contributing factors (previously computed in the browser);
* `coaching` — today's focus, one card per training domain (reading, action, progression, guard-off, cited evidence) and the personal patterns, all written by the engine;
* `clinical_intelligence` — score, band, tone, zone label and briefing wording;
* `data_quality` — per-metric origin plus the core-failure list.

If a new derived metric is needed, add it in `bio_analytics` (with its band in
`bio_policy`) and ship it resolved. Do not add a threshold ladder to the
frontend: that is how the page once displayed a recovery score of 68 labelled
"YELLOW" while its own prose called the same zone green.

## Inside the page

`index.html` is one file with three separable layers, in this order:

* **Markup** — the sections and cards, each with an ⓘ button that opens through the shared `toggleInfo()` helper. Copy is written for a reader who is not a clinician: the scientific name stays (HRV, RHR, ACWR) and the everyday meaning sits beside it, with the long form in brackets at first mention ("Overnight HRV (Heart Rate Variability)") and the why-it-matters sentence under the term rather than behind a click.
* **Render** — `renderDashboard(data)` walks the payload once and writes every value; each `render*` or `update*` helper owns one card. Values, bands and tones arrive resolved, so nothing here compares a metric with a threshold of its own.
* **Motion** — `primeMotion(scope)` owns every primitive, and three entry points apply it:
  the first paint (`playVisuals()`, at the end of `renderDashboard`) primes the document, a
  panel a render just rebuilt primes itself (`replayMotion(scope)` at the end of the render,
  e.g. `renderActivityTable`, `renderCoaching`, `renderFitbitPillars`,
  `renderConsistencyGrid`), and a second `renderDashboard` forgets every mark first so the
  readings it rewrote replay instead of being skipped as already played. It reads each
  element's already-rendered target and re-applies it, so no value is computed twice and a
  re-render never animates a number the engine did not publish. `vizPlayed` keeps a replay to
  once per element per pass and `forgetPlayed(scope)` deletes those marks without ever
  removing a class, so forgetting can never hide a reading; `motion-live` (added only when
  motion is allowed) is what arms the hidden start state, so a reduced-motion reader or a
  JavaScript-less load sees final values and no invisible cards. The helpers each own one
  shape of movement — `primeGauge` and `primeScale` (rings and the workload dial),
  `primeGrow` (bars and the battery, staggered by a delay per panel), `primeCount` (headline
  numbers, which stop writing the moment a render replaces their target), `animateHeatmap`
  (the heat map and its square enlarged view, one cell at a time) and the chart settle
  applied to every canvas — and the springs themselves live in CSS as `gauge-spring` /
  `bar-spring` / `chart-spring` / `heat-cell-spring`. `replayAnimation(el, class)` restarts
  one of those springs for an element the render rewrites in place rather than replaces;
  it adds no new keyframes. A canvas chart's own arrival is owned by the render that builds it: the quadrant matrix is constructed with every point at a far radius and each dataset's measured size is promoted on its own turn, so the nights come forward out of the screen one quadrant at a time. A `from`-based depth is deliberately avoided there, because Chart.js re-uses an animation config for every later transition and the entrance would replay on hover, collapsing the point under the reader's cursor. That arrival is one function on the chart instance (`arriveFromDepth`), played at the render and again by the reveal sweep: the sweep asks a canvas chart for its own arrival before it settles it generically, because resetting a chart whose points already sit where they were measured does nothing.

  A panel that is mostly readings and prose opts in with `data-stagger` (plus
  `data-stagger-flip` for the tipped entrance the glance strip uses): `staggerChildren`
  gives each child the same reveal, one step apart, so the panel assembles instead of
  appearing, whether that panel is being painted for the first time or rebuilt by a filter.
  It is opt-in per container because a grid and its cards should not both fade. Every one of
  these paths is armed only by `motion-live`, and each has a timer behind it — the sweep's
  frame latch, a primed bar, a counting number — so a throttled frame loop can never leave a
  measured value sitting invisible or wrong. Two traps the replay had to close are pinned by
  tests: an in-flight prime writes its own zero (or empty ring) as its start state, so a
  second prime in the same pass stands down instead of animating a reading down to nothing,
  and a reveal reads a chart instance when it runs rather than capturing it, because a
  render rebuilds the charts and resetting a destroyed one throws out of the sweep and
  leaves everything behind it unrevealed.

Analysis prose is split on `PLAIN_PARAGRAPH_SEPARATOR` into the clinical paragraph and the explanation beneath it; `asSentence()` stands policy's lowercase clauses alone on the lines that no longer carry an "In plain English:" label.

## Tests

`tests/test_pipeline.py` mirrors this structure: one test class per owner
(time, stress, provenance, baselines, circadian, strain/scores, snapshot,
model validation, payload assembly). `PayloadAssemblyTests` is the integration
guard — it runs the real `build_payload()` against a fake device client, so the
publish gate and the resolved-band contract are exercised offline.
