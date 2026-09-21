# Architecture

Six Python modules and one static page. Each concern has exactly one owner; if
you are about to write a threshold or a colour somewhere else, you are in the
wrong file.

```
                 garmin_source.py ──┐
   (the only Garmin I/O)            │
                                    ▼
                            bio_analytics.py ──────► clinical_engine.py
                            (pure arithmetic)        (verdicts: rules; model narrates)
                                    │                        │
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
| Thresholds, bands, tones, metric labels | `bio_policy.py` | Neither Python nor the browser may compare a metric against a number of its own; the payload carries resolved bands. `TONE_NAMES` is the whole colour vocabulary. |
| Arithmetic (baselines, windows, strain, sleep need, readiness, injury risk) | `bio_analytics.py` | Pure functions: values in, values out. No I/O, no environment, no provenance. Also decides positional windows ("last 30 days") and which record is latest. |
| Garmin access (auth, endpoints, parsing into records) | `garmin_source.py` | The only module that performs Garmin I/O. Records live/fallback provenance as it fetches. |
| Live/fallback bookkeeping and the publish decision | `provenance.py` | `DataQuality.publishable()` is the gate; the orchestrator consults it, nothing else decides. |
| Clinical synthesis | `clinical_engine.py` | The rule engine is the **only** author of the recovery score and of everything derived from it (band, tone, zone, readiness inputs, illness risk level, training target); Gemini is merged on top as narrative and its own score is kept as `model_score`, never published. Returns meaning, never layout or colour classes. |
| The run itself (fetch → analyse → publish → report) | `sync.py` | Wires the modules, assembles the payload envelope (`updated_at`, `athlete`, `history`, `policy`), writes `data/biometrics.json`, prints the summary. |
| Payload encryption and status file | `encrypt_data.py` | Standalone; reads the pipeline's JSON, never imports the pipeline. |
| Rendering and interaction | `index.html` | Consumes resolved bands/tones from the payload. The only decisions it makes are layout, and the only colours are `TONE_STROKE` / `badgeClass()` fed by the engine's tones. |

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
* `clinical_intelligence` — score, band, tone, zone label and briefing wording;
* `data_quality` — per-metric origin plus the core-failure list.

If a new derived metric is needed, add it in `bio_analytics` (with its band in
`bio_policy`) and ship it resolved. Do not add a threshold ladder to the
frontend: that is how the page once displayed a recovery score of 68 labelled
"YELLOW" while its own prose called the same zone green.

## Tests

`tests/test_pipeline.py` mirrors this structure: one test class per owner
(time, stress, provenance, baselines, circadian, strain/scores, snapshot,
model validation, payload assembly). `PayloadAssemblyTests` is the integration
guard — it runs the real `build_payload()` against a fake Garmin client, so the
publish gate and the resolved-band contract are exercised offline.
