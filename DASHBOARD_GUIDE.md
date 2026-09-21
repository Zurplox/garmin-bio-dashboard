# 🧬 Garmin Bio-Intelligence Terminal — User & Clinical Guide

A comprehensive, plain-English reference manual for Harvin's personal bio-intelligence dashboard. Designed for daily performance optimization, immune tracking, recovery planning, and longevity analytics without requiring medical training.

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
* **Why it matters:** Because the numbers never come from the model, the same overnight data always produces the same score, zone and training advice — a missing or failed model call changes the prose, never the verdict.
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

### 6. 🔋 Garmin Body Battery™ Dynamics (0 – 100)
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
  * 🔴 **> 1.5 ("Danger Zone"):** Exponentially increased injury risk due to sudden workload spikes.

---

### 11. ❤️ Overnight Heart Rate Variability (HRV)
* **What it is:** Microsecond variations between consecutive heartbeats (measured in milliseconds, ms).
* **The Core Principle:** **Higher HRV is better.** High HRV reflects a flexible, resilient parasympathetic nervous system ("rest and digest"). Low HRV indicates systemic fatigue, illness, dehydration, or psychological strain.
* **Personal Corridor:** 54 to 73 ms. Readings within or above this corridor indicate prime adaptation.

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

## ⌨️ Dashboard Hotkeys & Navigation

| Key | Action |
|:---:|:---|
| `1` – `9` | Jump directly to sections 1 through 9 |
| `O` | Toggle RHR dual-axis overlay on the HRV chart |
| `M` | Toggle tactile audio sound feedback |
| `P` | Open one-click Clinical Report Print dialog (clean PDF styling) |
| `R` | Refresh: re-read the published vault (and trigger a Garmin sync when a GitHub token is stored) |
| `L` | Instantly lock vault and purge decrypted data from browser memory |
| `ESC` | Dismiss any open modal |

---
*Maintained by Harvin (@Zurplox) // Powered by Garmin Connect™ API, a deterministic scoring engine, and optional Google Gemini AI narrative.*
