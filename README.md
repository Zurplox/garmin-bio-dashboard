# 🧬 Garmin Bio-Intelligence Terminal

An automated, end-to-end encrypted physiological dashboard and intelligence pipeline for athlete Harvin ([@Zurplox](https://github.com/Zurplox)), continuously syncing biometrics from Garmin Connect™ and computing deep biological baselines.

---

## 🔒 Zero-Exposure Security Architecture

Because this repository powers a public **GitHub Pages** deployment, all personal health and biometric data (`data/biometrics.enc.json`) is strictly **AES-256-GCM encrypted** at build/sync time using PBKDF2 (100,000 rounds) derived from the access passphrase.

* **Zero Plaintext Storage:** The unencrypted JSON dataset is `.gitignored` and is never committed to GitHub.
* **Client-Side In-Memory Decryption:** Browsers decrypt the ciphertext locally using the native W3C Web Crypto API (`window.crypto.subtle`). No health data is stored in cookies or plain files.
* **Dual-Tier Master Lock Protocol:**
  * **Viewer Tier (`Capybara`):** Decrypts and renders the interactive executive health dashboard.
  * **Master Lockout Tier (`Barabara`):** Emergency administrative lockout. Arming this master lock instantly purges decrypted data from memory and seals the terminal. Standard `Capybara` access is completely disabled until unlocked with the Master Passphrase.

---

## 📊 Tracked Physiological Metrics & Multi-Year Baselines

* **Sleep Architecture (90-Day High Resolution):** Overall Sleep Score, Deep Sleep (%), REM Sleep (%), Light Sleep, Restlessness, and nocturnal autonomic respiration.
* **Autonomic Nervous System & HRV:** Daily overnight HRV vs 7-day rolling baseline and personal normal range (54–73 ms).
* **Cardiovascular Efficiency (4-Year Trend, 2022–2026):** Longitudinal Resting Heart Rate (RHR) across 1,505+ days demonstrating cardiovascular fitness adaptation.
* **Energy Dynamics & Recovery:** Body Battery charging vs draining dynamics and all-day stress balance.
* **Recent Workout Feed:** Multi-sport activities, training load, aerobic/anaerobic training effects, and caloric expenditure.

---

## ⚙️ Automated Pipeline (GitHub Actions)

Scheduled to run automatically every day at **08:00 AM SGT (00:00 UTC)** with an automated **08:30 AM SGT** catch-up run:
1. Authenticates headlessly with Garmin SSO using encrypted session tokens in GitHub Secrets (`GARMIN_TOKENS`).
2. Ingests nocturnal sleep architecture, HRV stability, 4-year resting heart rate baselines, and workout activities.
3. Synthesizes clinical recovery and early infection warnings via **Google AI Studio (Gemini Flash)**.
4. Calculates Whoop 4.0 Strain/Sleep Need and Fitbit Premium 5-Pillar Health Metrics.
5. Encrypts payload with AES-256-GCM and deploys automatically to GitHub Pages.

---

*Built with Python, Garmin Connect API, Web Crypto API, Tailwind CSS, and Chart.js.*
