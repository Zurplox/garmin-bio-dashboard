"""
Clinical intelligence engine: turns a biometric snapshot into verdicts.

Two engines live behind one interface: Google Gemini when GEMINI_API_KEY is
present, and a deterministic rule engine that always runs. Model output is
validated before it is trusted -- numbers are coerced, bands are recomputed from
policy rather than taken from the model's own labels, and unusable output is
rejected so the deterministic path runs instead.

The engine returns meaning (bands, tones, verdicts), never layout: the dashboard
renders what it is handed.
"""

import json
import os
import urllib.request

import bio_policy as policy
from bio_analytics import clamp

GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.7-flash"]


def _zone_fields(recovery_score):
    """Recovery band, tone, short and long labels for a score -- all from policy."""
    band = policy.recovery_band(recovery_score)
    return {
        "recovery_band": band,
        "recovery_tone": policy.RECOVERY_TONES[band],
        "recovery_zone": policy.RECOVERY_ZONE_LABELS[band],
        "recovery_briefing": policy.RECOVERY_BRIEFING_LABELS[band],
    }


def _build_prompt(today, baselines, fitness):
    chrono_age = fitness.get("chronological_age", 29)
    device = fitness.get("device_name", "Garmin")
    return f"""
    You are an elite sports cardiologist and Whoop/Oura lead recovery scientist.
    Analyze the following biometrics for athlete Harvin ({chrono_age}M, software/farm executive in Singapore, training with {device}):

    BIOMETRICS SNAPSHOT:
    - Today Sleep Score: {today['sleep_score']}/100, Total Duration: {today['sleep_time_seconds']//3600}h {(today['sleep_time_seconds']%3600)//60}m
    - Deep Sleep: {today['deep_sleep_seconds']//3600}h {(today['deep_sleep_seconds']%3600)//60}m ({(today['deep_sleep_seconds']/today['sleep_time_seconds']*100):.1f}%) [6-month baseline: {baselines.get('deep_sleep_pct_180d')}%]
    - REM Sleep: {today['rem_sleep_seconds']//3600}h {(today['rem_sleep_seconds']%3600)//60}m ({(today['rem_sleep_seconds']/today['sleep_time_seconds']*100):.1f}%) [30d baseline: {baselines.get('rem_sleep_pct_30d')}%]
    - Overnight HRV: {today['hrv_last_night']} ms [Status: {today['hrv_status']}, 30d baseline: {baselines['hrv_30d']} ms, 6-month baseline: {baselines.get('hrv_180d')} ms, Corridor: {baselines['hrv_normal_range'][0]}-{baselines['hrv_normal_range'][1]} ms]
    - Resting Heart Rate: {today['rhr']} bpm [30d baseline: {baselines['rhr_30d']} bpm, 6-month baseline: {baselines.get('rhr_180d')} bpm, 4-year baseline: {baselines['rhr_all_time']} bpm]
    - Sleep Stress Index: {today['sleep_stress']}/100
    - Nocturnal Respiration: {today.get('respiration_rate', 13.0)} brpm [30d baseline: {baselines.get('respiration_avg_30d')} brpm]
    - Biological Fitness Age: {fitness.get('fitness_age', 24.7)} years (Chronological: {chrono_age})
    - Workload: Acute Load {fitness.get('acute_load', 44)}, Chronic Load {fitness.get('chronic_load', 219)}, ACWR {fitness.get('acwr', 0.2)} ({fitness.get('acwr_status', 'LOW')})
    - Body Battery: +{today['body_battery_charged']} charged

    Evaluate strictly. Output a single JSON object with these EXACT keys:
    1. "recovery_score": integer 0-100 (Whoop scale)
    2. "recovery_zone": "GREEN (OPTIMAL RECOVERY)", "YELLOW (ADEQUATE RECOVERY)", or "RED (HIGH NEUROLOGICAL STRAIN)"
    3. "illness_early_warning": {{
         "risk_level": "LOW", "MODERATE", or "HIGH",
         "status_headline": string,
         "delta_rhr_bpm": float,
         "delta_hrv_pct": float,
         "sleep_stress": float,
         "respiration_rate": float,
         "details": [string]
       }}
    4. "autonomic_nervous_analysis": string (detailed diagnostic paragraph on parasympathetic tone and vagal recovery)
    5. "sleep_architecture_analysis": string (detailed diagnostic paragraph on somatic physical vs cognitive REM repair and sleep debt)
    6. "workload_and_biological_age": string (detailed diagnostic paragraph on cardiovascular adaptation, ACWR ratio, and biological fitness age)
    7. "actionable_directives": array of 4 strings (1. workout target, 2. deep-work cognitive capacity, 3. caffeine cutoff time, 4. sleep hygiene directive)

    Output ONLY valid, parseable JSON without code fences or markdown blocks.
    """


def query_gemini_api(today, baselines, fitness):
    """Query Google AI Studio (Gemini Flash) when GEMINI_API_KEY is set."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    payload = {
        "contents": [{"parts": [{"text": _build_prompt(today, baselines, fitness)}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }

    for model in GEMINI_MODELS:
        print(f"🤖 Querying Google AI Studio ({model}) for clinical biometric synthesis...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text.startswith("```json"):
                    text = text[7:]
                elif text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                print(f"   ✅ Successfully received real {model} AI synthesis!")
                return json.loads(text.strip())
        except Exception as e:
            print(f"   ⚠️ {model} call failed ({e}). Trying next model...")

    print("   ⚠️ All Gemini models failed or timed out. Falling back to deterministic clinical rule engine.")
    return None


def normalize_ai_result(ai_result, today, baselines):
    """Validate and normalise model output before it reaches the dashboard.

    The prompt asks Gemini for a recovery score *and* a zone label derived from
    it, but nothing forces the two to agree -- the deployed vault contained a
    score of 68 labelled "YELLOW (ADEQUATE RECOVERY)", while the threshold table
    puts 68 in the green zone, so the page contradicted itself in adjacent
    paragraphs. Here the numbers are coerced, the band is recomputed from the
    score via policy, missing fields fall back to values this pipeline measured
    itself, and unusable output is rejected.
    """
    if not isinstance(ai_result, dict):
        return None

    def as_float(value, default=None):
        if value is None or isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    score = as_float(ai_result.get("recovery_score"))
    if score is None:
        return None
    recovery_score = int(clamp(round(score), 0, 100))

    warning = ai_result.get("illness_early_warning")
    if not isinstance(warning, dict):
        warning = {}
    risk = str(warning.get("risk_level") or "").upper()
    if risk not in policy.RISK_TONES:
        risk = "LOW"

    hrv_base = baselines.get("hrv_30d") or 0
    computed_delta_rhr = round((today.get("rhr") or 0) - (baselines.get("rhr_30d") or 0), 1)
    computed_delta_hrv = (
        round(((today.get("hrv_last_night") or 0) - hrv_base) / hrv_base * 100.0, 1) if hrv_base else 0.0
    )

    details = warning.get("details")
    if isinstance(details, str):
        details = [details]
    if not isinstance(details, list):
        details = []

    directives = ai_result.get("actionable_directives")
    if isinstance(directives, str):
        directives = [directives]
    if not isinstance(directives, list):
        directives = []
    directives = [str(d).strip() for d in directives if str(d).strip()][:6]

    def text_or(key, fallback):
        value = ai_result.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else fallback

    headline = warning.get("status_headline")
    if not isinstance(headline, str) or not headline.strip():
        headline = "Model risk assessment"

    return {
        "recovery_score": recovery_score,
        **_zone_fields(recovery_score),
        "illness_early_warning": {
            "risk_level": risk,
            "status_headline": headline.strip(),
            "delta_rhr_bpm": as_float(warning.get("delta_rhr_bpm"), computed_delta_rhr),
            "delta_hrv_pct": as_float(warning.get("delta_hrv_pct"), computed_delta_hrv),
            "sleep_stress": as_float(warning.get("sleep_stress"), today.get("sleep_stress", 0.0)),
            "respiration_rate": as_float(
                warning.get("respiration_rate"), today.get("respiration_rate", 0.0)
            ),
            "details": [str(d) for d in details],
        },
        "autonomic_nervous_analysis": text_or("autonomic_nervous_analysis", ""),
        "sleep_architecture_analysis": text_or("sleep_architecture_analysis", ""),
        "workload_and_biological_age": text_or("workload_and_biological_age", ""),
        "actionable_directives": directives,
        "source": "gemini",
    }


def _illness_screen(today, baselines):
    """Deterministic illness radar. Thresholds come from policy; every number in
    the prose is measured here rather than asserted."""
    hrv = today.get("hrv_last_night", 60)
    hrv_base = baselines.get("hrv_30d", 55.3) or 1
    rhr = today.get("rhr", 51.0)
    rhr_base = baselines.get("rhr_30d", 49.8)
    sleep_stress = today.get("sleep_stress", 16.0)
    resp = today.get("respiration_rate", 13.0)

    delta_rhr = rhr - rhr_base
    delta_hrv_pct = ((hrv - hrv_base) / hrv_base) * 100.0

    if delta_rhr >= policy.ILLNESS_HIGH_DELTA_RHR and delta_hrv_pct <= policy.ILLNESS_HIGH_DELTA_HRV_PCT:
        risk = "HIGH"
        headline = "Elevated Immune Activation (High Sickness Probability)"
        details = [
            "Significant RHR elevation ({:+.1f} bpm above your 30-day baseline) coupled with severe "
            "HRV depression ({:.1f}%). High likelihood of systemic inflammatory or viral load.".format(
                delta_rhr, delta_hrv_pct
            )
        ]
    elif (
        delta_rhr >= policy.ILLNESS_MODERATE_DELTA_RHR
        or delta_hrv_pct <= policy.ILLNESS_MODERATE_DELTA_HRV_PCT
        or sleep_stress > policy.ILLNESS_SLEEP_STRESS_MAX
    ):
        risk = "MODERATE"
        headline = "Mild Systemic Strain (Watch Recovery Closely)"
        details = [
            "Slight autonomic elevation detected ({:+.1f} bpm RHR, {:.1f}% HRV, sleep stress {}/100). "
            "Monitor hydration, body temperature, and reduce training intensity.".format(
                delta_rhr, delta_hrv_pct, sleep_stress
            )
        ]
    else:
        risk = "LOW"
        headline = "Immune Vitals Normal (No Inflammatory or Infection Markers)"
        details = [
            "RHR deviation is minimal ({:+.1f} bpm) and HRV sits {:.1f}% versus baseline. Nocturnal "
            "respiratory rate ({} brpm) and sleep stress ({}/100) show no immune activation.".format(
                delta_rhr, delta_hrv_pct, resp, sleep_stress
            )
        ]

    return risk, headline, details, delta_rhr, delta_hrv_pct


def deterministic_engine(today, baselines, fitness):
    """Rule engine used when Gemini is unavailable, rejected, or contradictory.

    Every sentence is assembled from values measured in this run: the previous
    version hard-coded its verdicts ("Peak 5-minute HRV reached 89 ms", a
    "+4.3 years younger" advantage, a fixed 22:15 wind-down) even when the
    measurements said otherwise.
    """
    hrv = today.get("hrv_last_night", 60)
    hrv_base = baselines.get("hrv_30d", 55.3) or 1
    sleep_stress = today.get("sleep_stress", 16.0)
    resp = today.get("respiration_rate", 13.0)
    sleep_sec = today.get("sleep_time_seconds", 23700)
    deep_sec = today.get("deep_sleep_seconds", 5580)
    rem_sec = today.get("rem_sleep_seconds", 3780)

    illness_risk, illness_status, illness_details, delta_rhr, delta_hrv_pct = _illness_screen(
        today, baselines
    )

    hrv_factor = min(max((hrv / hrv_base) * 35, 15), 45)
    sleep_hours = sleep_sec / 3600.0
    sleep_factor = min(max((sleep_hours / policy.TARGET_SLEEP_HOURS) * 35, 15), 35)
    rhr_factor = 15 if delta_rhr <= 1.0 else max(15 - (delta_rhr * 3), 0)
    stress_factor = 15 if sleep_stress <= 20 else max(15 - ((sleep_stress - 20) * 0.5), 0)

    recovery_score = int(clamp(hrv_factor + sleep_factor + rhr_factor + stress_factor, 10, 99))

    deep_pct = (deep_sec / sleep_sec * 100) if sleep_sec else 23.5
    rem_pct = (rem_sec / sleep_sec * 100) if sleep_sec else 16.0
    sleep_debt_minutes = max(int((policy.TARGET_SLEEP_HOURS - sleep_hours) * 60), 0)

    deep_base = baselines.get("deep_sleep_pct_180d", 22.0)
    deep_verdict = "above" if deep_pct >= deep_base else "below"
    rem_verdict = "above" if rem_pct >= 16.0 else "below"
    sleep_verdict = (
        f"Sleep architecture shows Deep Sleep at {deep_pct:.1f}% "
        f"({deep_sec // 3600}h {(deep_sec % 3600) // 60}m) -- {deep_verdict} your 6-month baseline "
        f"of {deep_base}% for muscular and tissue repair. REM Sleep logged at {rem_pct:.1f}% "
        f"({rem_sec // 3600}h {(rem_sec % 3600) // 60}m), {rem_verdict} the 16% reference, with "
        f"{sleep_debt_minutes} min of debt against the {policy.TARGET_SLEEP_HOURS:g}h target. "
        f"Nightly stress at {sleep_stress}/100."
    )

    hrv_band = policy.hrv_band(hrv, hrv_base)
    hrv_corridor = baselines.get("hrv_normal_range", [54, 73])
    autonomic_verdict = (
        f"Autonomic tone is {'BALANCED' if hrv_band == 'above' else 'SUPPRESSED' if hrv_band == 'below' else 'DEGRADED'}: "
        f"overnight HRV averaged {hrv} ms ({delta_hrv_pct:+.1f}% versus your 30-day baseline of "
        f"{hrv_base} ms and 6-month baseline of {baselines.get('hrv_180d')} ms), against a normal "
        f"physiological band of {hrv_corridor[0]}-{hrv_corridor[1]} ms. Resting heart rate moved "
        f"{delta_rhr:+.1f} bpm versus its 30-day baseline."
    )

    chrono_age = fitness.get("chronological_age", 29)
    fitness_age = fitness.get("fitness_age", 24.7)
    age_advantage = round(chrono_age - fitness_age, 1)
    acwr = fitness.get("acwr", 0.2)
    workload_verdict = (
        f"Biological Fitness Age stands at {fitness_age} years -- {'operating' if age_advantage > 0 else 'trailing by'} "
        f"{abs(age_advantage):.1f} years versus your chronological age of {chrono_age}. "
        f"Acute-to-Chronic Workload Ratio (ACWR) is {acwr} with an Acute Load of "
        f"{fitness.get('acute_load', 44)} against a Chronic Load of {fitness.get('chronic_load', 219)}, "
        f"a {policy.acwr_workload_band(_as_float(acwr, 0.2)).lower()} workload band."
    )

    heat = fitness.get("heat_acclimation_pct")
    directives = [
        {
            "prime": "Cardiovascular / Physical Target: green light for high-intensity interval training, heavy resistance work, or high-volume Zone 2 cardio.",
            "ready": "Cardiovascular / Physical Target: moderate-to-hard training is appropriate -- prioritise quality over volume.",
            "easy": "Cardiovascular / Physical Target: keep intensity low. Walking, mobility, or easy Zone 1 movement only.",
            "rest": "Cardiovascular / Physical Target: full rest. Training today would deepen the recovery deficit.",
        }[policy.readiness_band(recovery_score)],
        "Cognitive Demand: schedule your most demanding deep-work sessions before the afternoon and protect 90-minute blocks.",
        "Caffeine Cutoff: stop caffeine at least 8 hours before your recommended bedtime (see the sleep architecture card).",
        "Sleep Hygiene: begin a screen-free wind-down 30 minutes before your recommended bedtime to protect REM duration.",
    ]
    if heat is not None:
        directives.append(
            f"Hydration & Heat Acclimation: Singapore heat acclimation currently reads {heat}%; "
            "maintain 3.0L electrolyte-supported fluid intake."
        )

    return {
        "recovery_score": recovery_score,
        **_zone_fields(recovery_score),
        "source": "deterministic",
        "illness_early_warning": {
            "risk_level": illness_risk,
            "status_headline": illness_status,
            "delta_rhr_bpm": round(delta_rhr, 1),
            "delta_hrv_pct": round(delta_hrv_pct, 1),
            "sleep_stress": sleep_stress,
            "respiration_rate": resp,
            "details": illness_details,
        },
        "sleep_architecture_analysis": sleep_verdict,
        "autonomic_nervous_analysis": autonomic_verdict,
        "workload_and_biological_age": workload_verdict,
        "actionable_directives": directives,
    }


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def synthesize(today, baselines, fitness):
    """Gemini first, deterministic engine as the guaranteed fallback."""
    ai_result = query_gemini_api(today, baselines, fitness)
    if ai_result:
        normalized = normalize_ai_result(ai_result, today, baselines)
        if normalized:
            return normalized
        print("   ⚠️ Gemini output failed validation; falling back to the deterministic clinical engine.")
    return deterministic_engine(today, baselines, fitness)
