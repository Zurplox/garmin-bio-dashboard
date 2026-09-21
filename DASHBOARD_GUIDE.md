# 🧬 Meridian Bio-Intelligence Terminal — User & Clinical Guide

A comprehensive, plain-English reference manual for Harvin's personal bio-intelligence dashboard. Designed for daily performance optimization, immune tracking, recovery planning, and longevity analytics without requiring medical training.

**A note on where the personal details come from.** Every fact about the athlete on this dashboard is measured: age, sex, height, weight, BMI, VO₂max and threshold heart rate come from the linked account's own profile, and location, climate, blood oxygen and training totals come from the device's own records. Nothing is assumed about occupation, diet, family, schedule or living situation, and a value the device did not measure reads `--` rather than a plausible substitute.

---

## 🌐 Dashboard Access & Security

* **Live URL:** [https://zurplox.github.io/garmin-bio-dashboard/](https://zurplox.github.io/garmin-bio-dashboard/)
* **Standard Viewer Passphrase:** `Capybara` *(Auto-remembered in browser via Web Storage)*
* **Emergency Master Lock Passphrase:** `Barabara` *(Administrative lockout)*
* **Data Security:** Zero plaintext health data on GitHub. All biometrics are client-side encrypted using **AES-256-GCM** (PBKDF2-HMAC-SHA256, 600,000 iterations per OWASP's current guidance; the envelope records the cost it was written with and the browser honours that field). Decryption occurs exclusively in browser memory.
* **Sync Frequency:** Synchronizes every morning at **08:00 AM SGT (00:00 UTC)** via GitHub Actions, with an automated 08:30 AM SGT retry buffer.

---

## 🧭 Complete Metric Guide (Plain-English Translations)

### 1. 🧠 Daily Briefing
* **What it is:** A personalized daily executive briefing combining your overnight sleep, recovery, stress, and workload trends. The narrative paragraphs can be written by Google Gemini AI when a key is configured; the **Recovery and Readiness numbers inside it are always computed by the deterministic rule engine** from your own measured baselines. The badge above the briefing names which engine wrote the words, and reads `Deterministic rule engine` when no model key is present.
* **Read the last sentence of each paragraph first.** Every analysis paragraph — whether the rule engine or Gemini wrote it — ends with a sentence beginning **`In plain English:`** that restates the same finding in everyday words, written by the rule engine from your own measurements. The clinical sentences above it carry the detail and the exact figures; the closing sentence is what it means for today. Example: *"Autonomic tone is Below Baseline… **In plain English: your overnight recovery signal reads 52 ms against your own 30-day average of 55.1 ms, so this is an ordinary day-to-day dip — train as planned, but do not chase a personal best.**"*
* **Why it matters:** Because the numbers never come from the model, the same overnight data always produces the same score, zone and training advice — a missing or failed model call changes the prose, never the verdict. The plain-English sentence is written by the rules for the same reason, so a dense or abstract model paragraph still ends with your takeaway.
* **How to read it:** Scan the greeting and the 4 status indicators (Recovery, HRV, Readiness, Injury Risk). Green indicates primed capacity; amber signals moderate fatigue; red indicates required rest.

---

### 2. ⚡ Bio-Recovery Engine (Recovery Score: 0–100%)
* **What it is:** Your daily "readiness percentage" indicating how completely your autonomic nervous system and cardiovascular reserves recharged during sleep.
* **How to read it:**
  * 🟢 **Green (67% – 100%):** Prime recovery. Your cardiovascular system and nervous system are fully recovered. Ideal day for maximum effort, high-intensity intervals, or PRs.
  * 🟡 **Yellow (34% – 66%):** Moderate recovery. Good for maintenance workouts, technical practice, or steady-state endurance.
  * 🔴 **Red (0% – 33%):** Recovery deficit. Cellular and nervous repair are incomplete. Prioritize rest, hydration, walking, or mobility work.

---

### 3. 🎯 Day Strain & Target Strain Window (0.0 – 21.0)
* **What it is:** A logarithmic scale tracking cumulative cardiovascular load across 24 hours (exercise, active movement, elevated heart rate, and physiological stress).
* **Scale Breakdown:**
  * **0.0 – 9.9:** Light day (active recovery, rest, desk work).
  * **10.0 – 13.9:** Moderate strain (aerobic base training, brisk walk, functional gym work).
  * **14.0 – 17.9:** Heavy training (stimulates cardiovascular adaptation and endurance).
  * **18.0 – 21.0:** All-out maximal effort (race day or extreme volume).
* **Target Strain Window:** Dynamically calibrated based on today's Recovery Score. Staying within your target window guarantees progressive fitness gains without overreaching.

---

### 4. 🛏️ Sleep Need & Bedtime Architecture
* **What it is:** A mathematically formulated sleep target that factors in your genetic baseline, physical strain accumulation, and accumulated sleep debt.
* **The Formula:**
  $$\text{Total Sleep Need} = \text{Baseline Need (7h 30m)} + \text{Sleep Debt Paydown} + \text{Strain Need}$$
* **Sleep Debt:** Accumulated hours of sleep deficit over the prior 7 nights. Repaying this gradually (+30–60 min/night) restores cognitive sharpness and growth hormone secretion.
* **Target Bedtime Window:** Reverse-engineered from your wake alarm and melatonin sleep gate to maximize 90-minute ultradian sleep cycles.

---

### 5. 🛡️ Illness & Infection Early Warning Radar
* **What it is:** An algorithmic diagnostic system scanning for subclinical signs of viral infection, systemic inflammation, or overtraining syndrome up to 48 hours before physical symptoms manifest.
* **Key Diagnostic Biomarkers:**
  * **Δ RHR vs Base:** A spike of $+3.0\text{ bpm}$ or higher often indicates immune defense activation.
  * **Δ HRV vs Base:** A sudden suppression of $-15\%$ or worse indicates sympathetic stress or immune fight.
  * **Nocturnal Respiration:** Breaths per minute during sleep. Elevated breathing ($>15.0\text{ brpm}$) is a clinical marker of fever or respiratory infection.
  * **Sleep Stress Index:** Measures overnight fight-or-flight activity. Values below 25 confirm deep parasympathetic restorative state.

---

### 6. 🔋 Body Battery Dynamics (0 – 100)
* **What it is:** An energy reserve gauge tracking real-time physical and nervous energy depletion vs. charging.
* **Charging (+pts charged):** How many energy points your restorative sleep replenished. $+35\text{ to }+50\text{ points}$ represents an optimal recharge.
* **Drain Rate:** Measures daytime energy expenditure from work, movement, and stress.

---

### 7. ⏱️ 24-Hour Stress Architecture (Firstbeat Autonomics)
* **What it is:** Continuous autonomic nervous system tracking dividing your day into 4 physiological states:
  * 🟢 **Rest (0–25):** Cellular repair, hormone production, and relaxation. *(Optimal target: >50% of day)*
  * 🔵 **Low (26–50):** Calm focus, light physical movement, routine work.
  * 🟡 **Medium (51–75):** Significant physical exertion or intense mental focus.
  * 🔴 **High (76–100):** Fight-or-flight sympathetic overdrive.

---

### 8. 🌙 Circadian & Ultradian Sleep Gate
* **What it is:** Alignment of your sleep window with your biological chronotype and 90-minute REM/Deep sleep cycles.
* **Ultradian Sleep Cycles:** A complete human sleep cycle lasts approximately 90 minutes. 4 to 5 complete cycles ($6.0\text{ to }7.5\text{ hours}$) are essential for human growth hormone (HGH) release and neural memory consolidation.
* **Melatonin Gate:** The optimal 30-minute window in the evening when natural melatonin peaks, allowing rapid sleep onset.

---

### 9. 🧬 Biological Fitness Age
* **What it is:** An empirical calculation comparing your cardiovascular VO2 max, resting heart rate, and body composition against population percentiles.
* **How to interpret:** A Biological Age below chronological age (e.g., **24.7 years vs 29 chronological**, a **-4.3 year advantage**) confirms that your cardiovascular system operates with the vitality of someone significantly younger.

---

### 10. ⚖️ Acute:Chronic Workload Ratio (ACWR)
* **What it is:** The gold-standard sports science metric comparing your last 7 days of training ("Acute Load") against your rolling 28-day training base ("Chronic Load").
* **How to read:**
  * 🟢 **0.8 – 1.3 ("Sweet Spot"):** Safe, progressive fitness building with minimized injury risk.
  * 🔵 **< 0.8 ("Fresh / Under-trained"):** Low fatigue, high readiness; capacity to safely add workload.
  * 🟠 **1.3 – 1.5 ("High"):** Approaching the danger zone; hold volume where it is rather than adding more.
  * 🔴 **> 1.5 ("Danger Zone"):** Exponentially increased injury risk due to sudden workload spikes.
* **These four names are what the dashboard shows everywhere** — the ACWR badge, the workload band on the Training Readiness card and the ACWR info panel all use them, so the same ratio can never be described two ways. The watch's own status word for the ratio is kept in the ACWR panel as context only.

---

### 11. ❤️ Overnight Heart Rate Variability (HRV)
* **What it is:** Microsecond variations between consecutive heartbeats (measured in milliseconds, ms).
* **The Core Principle:** **Higher HRV is better.** High HRV reflects a flexible, resilient parasympathetic nervous system ("rest and digest"). Low HRV indicates systemic fatigue, illness, dehydration, or psychological strain.
* **Personal Corridor:** the band your watch reports for the current period (it reads as `54 – 72 ms` on the dashboard when the last publish carried a band of 54–72). Readings within or above this corridor indicate prime adaptation.

---

### 12. 🩺 Resting Heart Rate (RHR)
* **What it is:** Lowest heart rate during deep, uninterrupted rest.
* **Benchmarks:**
  * Elite Endurance: 38 – 48 bpm
  * Athletic: 48 – 58 bpm *(Harvin's range: ~49–51 bpm)*
  * Average Adult: 60 – 80 bpm
* **Longitudinal Adaptation:** Harvin's 4-year multi-year baseline shows steady progression from 53.1 bpm (2022) to sub-50 bpm (2025–2026), proving cardiac stroke volume improvement.

---

### 13. 📊 2D Autonomic Nervous System Quadrant Matrix
* **What it is:** A 186-night scatter plot mapping overnight HRV (vertical Y-axis) against Resting Heart Rate (horizontal X-axis).
* **The 4 Recovery Quadrants:**
  * 🟢 **Q1: Peak Recovery (Top-Left):** High HRV + Low RHR. Nervous system relaxed, cardiovascular load low. Prime training state (84+ nights).
  * 🟡 **Q2: Cardiovascular Arousal (Top-Right):** High HRV + Elevated RHR. High autonomic tone with metabolic/thermal afterburn.
  * 🟣 **Q3: Parasympathetic Fatigue (Bottom-Left):** Low HRV + Low RHR. Nervous system exhausted or suppressed; deep restorative rest needed.
  * 🔴 **Q4: Systemic Strain (Bottom-Right):** Low HRV + Elevated RHR. Acute stress, overtraining, dehydration, or early illness.

---

### 14. 🏋️ Training Readiness Score (0 – 100)
* **What it is:** A single unified readiness score combining HRV departure from 30d baseline, Recovery Score, recent ACWR load, and sleep debt.
* **Badges:** `PRIME` (75+), `READY` (50–74), `EASY DAY` (25–49), `REST` (0–24).

---

### 15. ⚠️ Injury Risk Index (0 – 100%)
* **What it is:** A sports medicine risk model evaluating workload spikes, rapid HRV drops, accumulated sleep debt, and consecutive high-strain sessions.
* **Values:**
  * **< 25% (LOW):** Safe to progressively push volume or intensity.
  * **25% – 55% (MODERATE):** Monitor training volume; avoid sudden intensity jumps.
  * **> 55% (HIGH):** Deload immediately; prioritize recovery and tissue repair.

---

### 16. 🫁 Blood Oxygen (SpO₂)
* **What it is:** The share of your red blood cells carrying oxygen, measured by the light sensor on the back of the watch. At sea level most healthy adults sit between **95% and 100%**; a sustained reading below **90%** is the point at which a doctor should look at it.
* **Why your series looks patchy:** Pulse Ox is an on-demand sensor. It only records when the watch is still and sitting flat on the wrist, which in practice means some days and not others — **9% of the last 120 days** on the current data, with one overnight average in the window. The card therefore states how many days actually carried a reading, and every reading is one sample rather than a nightly average.
* **Values:**
  * **In Range (≥ 95%):** Nothing to do; this is the normal band.
  * **Slightly Low (92–94%):** Worth watching if it repeats. A cold hand, a loose strap, altitude or a mild illness all move a single reading.
  * **Low (< 92%):** Below the normal band. If it persists, raise it with a doctor rather than with training.
  * **Single dip:** A flagged point is one reading at 90% or below — one dip is a data point, not a diagnosis.
* **To get a reading every night:** set **Pulse Ox → During Sleep** in the watch settings. Until then, treat each reading as a single sample.

### 17. 🗺️ Terrain & Climate (Location, Heat, Hydration)
* **What it is:** Where your sessions actually happened — taken from the location the watch logged with each activity — what the air was doing at the most recent one, and how adapted your body is to training in that heat.
* **Values:**
  * **Where you train:** the place your device logged most often (currently Singapore). Anywhere else is listed as travel with the sessions and dates, so a trip shows up as a trip and nothing is assumed about where you live.
  * **Temperature, humidity and feels-like:** measured by the weather station your device paired with your last session (currently 27.2 °C, 89% humidity, feels 30.6 °C, Seletar Airport). These are that session's conditions, not a forecast.
  * **Heat acclimation:** your device's own estimate of heat adaptation. **Not Acclimated** (below 20%) means a hot session costs you more heart-rate work; **Partly Acclimated** (20–49%) is mid-adaptation; **Heat Acclimated** (50%+) means your body has adapted and the same pace costs less. It fades after roughly a week without hot sessions.
  * **Hydration:** your daily target (2.9 L), what you logged (0 L — nothing logged), and the fluid your latest session sweated (786 ml).

### 18. 🏃 Capacity & Forecast (VO₂max, BMI, Race Times, Intensity Minutes)
* **What it is:** Your aerobic ceiling and what the device projects from it, all read from your own profile and records.
* **Values:**
  * **VO₂max (51.2 ml/kg/min):** the most oxygen your body can use per minute per kilogram — the standard single number for aerobic capacity. A rise of one or two points over months of consistent training is a real improvement.
  * **BMI (22.8, In Range):** computed from the height and weight on your profile, not assumed. Below 18.5 is under range, 18.5–24.9 in range, 25–29.9 above range, 30+ high.
  * **Threshold HR (166 bpm):** the heart rate at which your body starts accumulating lactate faster than it clears it — the ceiling for a hard but sustainable effort.
  * **Race forecast (5K 25:53 · 10K 54:55 · Half 2h03 · Marathon 4h29):** the device's own projection from your recent running, which assumes you keep training the way you have been. It is a fair estimate of current fitness rather than a promise.
  * **Intensity minutes (20 of 150 this week, Below Target):** moderate-equivalent minutes of movement; vigorous minutes count double. The 150-minute weekly figure is the widely used guideline, and most of this week has been easy walking rather than moderate exercise.
  * **Today:** steps against your own goal, floors climbed and active calories.

### 19. 🔬 Correlation Lab (How Your Own Signals Move Together)
* **What it is:** Patterns computed from your own paired days — for example, whether nights with a higher sleep score are also nights with a higher HRV. Each published pattern carries its coefficient (`r`), the number of paired days behind it, and a plain-English note explaining why that pairing is physiologically plausible.
* **How to read `r`:** near **+1** the two readings rise and fall together; near **−1** one rises as the other falls; near **0** they are unrelated. The day count is how many nights **both** were measured, so `r = −0.70` across 109 nights is a pattern and `r = 0.5` across 14 nights is a hint.
* **What is deliberately hidden:** only pairings with **at least 14 paired days** and **|r| ≥ 0.35** are published, and only pairings with a plausible physiological reason are tested at all. Testing every combination of channels would eventually produce a strong-looking number by chance — the fixed rate of false positives that makes most "insights" panels untrustworthy.
* **Location check:** the same comparison run across your own trips. Over 5 travel days (12 sessions, mostly Quan 1) against 40 days at home in Singapore: overnight HRV **54.0 ms** away versus **59.6 ms** at home, resting heart rate **52.0** away versus **49.0** at home, sleep score **70.2** away versus **81.2** at home, sleep duration **6.0 h** away versus **7.0 h** at home. Read that as travel being expensive for your recovery, not as proof of why. Two things to hold in mind: "away" means your device logged a session somewhere else (not necessarily where you slept), and a correlation is a pattern in your data, never proof of cause.

### 20. 🧑‍🏫 Coach (What To Do About It)
* **What it is:** the actionable layer over everything above. One card per training domain the device actually records — **Recovery & Load**, **Strength & Muscle**, **Running & Hard Cardio**, **Walking & Daily Movement**, **Hiking & Hills**, **Sleep** — plus a single **Today's Focus** that picks the one action worth doing today.
* **Every card has the same five parts:** the reading (what your numbers say now), the plain-English restatement, **Do this** (one concrete action), **Progress** (how it grows over the next two weeks) and **Back off when** (the guard-rail that cancels the action).
* **Each rule cites its study.** Every card carries a button that opens the source, what it found, and — importantly — **what it does not settle**. Examples: keeping the load ratio in the 0.8–1.3 band (Gabbett, *Br J Sports Med* 2016, with the caveat that Zouhal 2021 and Maupin 2020 dispute its strength); training each muscle group at least twice a week (Schoenfeld, *Sports Med* 2016); walking towards 8,000 steps rather than 10,000 (Paluch, *Lancet Public Health* 2022); steady bedtimes mattering more than long ones (Windred, *Sleep* 2024); heat adaptation decaying about 2.5% a day without exposure (Daanen, *Sports Med* 2018); and judging a hike by its climb because energy cost rises steeply with gradient (Minetti, *J Appl Physiol* 2002).
* **A domain with no history says so.** No strength sessions in 28 days, no runs, no logged climb — each renders as **NOT MEASURED** with a neutral tone and an empty metric row, rather than a generic tip dressed up as coaching.
* **Know Yourself panel:** patterns mined from your own history, each with the number of days behind it. Currently published: your HRV after a hard day versus after rest, sleep score after a late bedtime, steps the day after training, which weekday carries your load, and your **metres per heartbeat** trend across runs (more ground at the same heart rate means the aerobic engine grew). These are associations over your own data, not causes — the panel says so, and a pattern is withheld until it has enough paired days.
* **Which card wins today** is a stated policy order (recovery → sleep → strength → endurance → hiking → walking) applied after urgency, so a red morning or a dangerous load ratio always outranks a volume target.

---

## ⌨️ Dashboard Hotkeys & Navigation

| Key | Action |
|:---:|:---|
| `1` – `9` | Jump directly to sections 1 through 9 |
| `O` | Toggle RHR dual-axis overlay on the HRV chart |
| `M` | Toggle tactile audio sound feedback |
| `P` | Open one-click Clinical Report Print dialog (clean PDF styling) |
| `R` | Refresh: re-read the published vault (and trigger a sync when a GitHub token is stored) |
| `L` | Instantly lock vault and purge decrypted data from browser memory |
| `ESC` | Dismiss any open modal |

---
*Maintained by Harvin (@Zurplox) // Meridian: device-recorded biometrics, a deterministic scoring engine, and an optional AI narrative layer whose closing plain-English sentence is always written by the rules.*
