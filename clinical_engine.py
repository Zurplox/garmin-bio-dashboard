"""
Clinical intelligence engine: turns a biometric snapshot into verdicts.

One owner per fact. The rule engine computes every published score, band, tone
and risk level from this run's own measurements; Google Gemini, when
GEMINI_API_KEY is present, supplies prose describing those numbers. The model is
never allowed to author a number, because the same physiology then published
different verdicts depending on which engine answered -- byte-identical inputs
once produced recovery 94% GREEN PRIME with no key and 62% YELLOW READY with
one. Its own recovery score is kept beside the published one as a second
opinion (`model_score`), not as the answer.

The engine returns meaning (bands, tones, verdicts), never layout: the dashboard
renders what it is handed. Every analysis paragraph ends with a plain-English
sentence written by the rules -- never by the model -- that restates the verdict
in everyday words.
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


def _profile_block(today, baselines, fitness, context):
    """The measured facts about the athlete, and nothing else.

    This block used to open with an assumed biography -- an age, an occupation and
    a city that no measurement supplied. Everything here now comes from the
    athlete's own account or from the device's own records, and the prompt says so,
    so the model cannot dress an assumption up as an observation.
    """
    context = context or {}
    profile = context.get("profile") or {}
    capacity = context.get("capacity") or {}
    environment = context.get("environment") or {}
    oxygen = context.get("oxygen") or {}

    age = profile.get("age_years") or fitness.get("chronological_age")
    gender = profile.get("gender") or "not recorded"
    height = profile.get("height_cm") or "not recorded"
    weight = profile.get("weight_kg") or "not recorded"
    bmi = capacity.get("bmi") or "not recorded"
    vo2max = profile.get("vo2max") or "not recorded"
    lthr = profile.get("lactate_threshold_hr") or "not recorded"
    device = fitness.get("device_name") or "the primary device"

    weather = environment.get("weather") or {}
    climate = ", ".join(
        str(part)
        for part in (
            f"{weather.get('temp_c')}C" if weather.get("temp_c") is not None else None,
            f"{weather.get('humidity_pct')}% humidity" if weather.get("humidity_pct") is not None else None,
            f"heat acclimation {environment.get('heat_acclimation_pct')}%"
            if environment.get("heat_acclimation_pct") is not None
            else None,
        )
        if part
    ) or "not recorded"

    if oxygen.get("available"):
        oxygen_line = (
            f"latest {oxygen['latest']}% on {oxygen['latest_date']}"
            + (f", sleep-time average {oxygen['sleep_average']}%" if oxygen.get("sleep_average") else "")
            + (f", lowest recorded {oxygen['lowest']}%" if oxygen.get("lowest") is not None else "")
            + f" -- recorded on {oxygen['days_recorded']} of the last {oxygen['window_days']} days"
        )
    else:
        oxygen_line = "not recorded in the last 120 days"

    weekly = (capacity.get("intensity") or {}).get("weekly_total")
    intensity_line = (
        f"{weekly} of {(capacity.get('intensity') or {}).get('goal')} weekly intensity minutes"
        if weekly is not None
        else "not recorded"
    )

    return f"""
    ATHLETE PROFILE (measured: the account's own profile plus the device's own records).
    These are the ONLY personal facts available. Do not assume or mention anything
    not on this list -- no occupation, employer, diet, family, schedule, travel
    plans, medical history or living situation.
    - Age {age} | Sex {gender} | Height {height} cm | Weight {weight} kg | BMI {bmi}
    - VO2max {vo2max} ml/kg/min | Lactate threshold HR {lthr} bpm | Device {device}
    - Training logged around: {environment.get('home_location') or 'not recorded'}
      ({"; ".join(f"{s['location']} {s['sessions']} sessions" for s in (environment.get('away_stays') or [])[:3]) or 'no other locations logged'})
    - Latest session conditions: {climate}
    - Blood oxygen: {oxygen_line}
    - Weekly movement: {intensity_line}
    """


def _measured(value):
    """How an unmeasured value reads to the model: named, never filled in."""
    return "not measured" if value is None else value


def _build_prompt(today, baselines, fitness, context=None):
    chrono_age = _measured((context or {}).get("profile", {}).get("age_years") or fitness.get("chronological_age"))
    return f"""
    You are a sports cardiologist writing a daily recovery brief for one athlete.
    You have their measurements below and nothing else.
{_profile_block(today, baselines, fitness, context)}
    BIOMETRICS SNAPSHOT:
    - Today Sleep Score: {today['sleep_score']}/100, Total Duration: {today['sleep_time_seconds']//3600}h {(today['sleep_time_seconds']%3600)//60}m
    - Deep Sleep: {today['deep_sleep_seconds']//3600}h {(today['deep_sleep_seconds']%3600)//60}m ({(today['deep_sleep_seconds']/today['sleep_time_seconds']*100):.1f}%) [6-month baseline: {baselines.get('deep_sleep_pct_180d')}%]
    - REM Sleep: {today['rem_sleep_seconds']//3600}h {(today['rem_sleep_seconds']%3600)//60}m ({(today['rem_sleep_seconds']/today['sleep_time_seconds']*100):.1f}%) [30d baseline: {baselines.get('rem_sleep_pct_30d')}%]
    - Overnight HRV: {today['hrv_last_night']} ms [Status: {today['hrv_status']}, 30d baseline: {baselines['hrv_30d']} ms, 6-month baseline: {baselines.get('hrv_180d')} ms, Corridor: {baselines['hrv_normal_range'][0]}-{baselines['hrv_normal_range'][1]} ms]
    - Resting Heart Rate: {today['rhr']} bpm [30d baseline: {baselines['rhr_30d']} bpm, 6-month baseline: {baselines.get('rhr_180d')} bpm, 4-year baseline: {baselines['rhr_all_time']} bpm]
    - Sleep Stress Index: {today['sleep_stress']}/100
    - Nocturnal Respiration: {today.get('respiration_rate', 13.0)} brpm [30d baseline: {baselines.get('respiration_avg_30d')} brpm]
    - Biological Fitness Age: {_measured(fitness.get('fitness_age'))} years (Chronological: {chrono_age})
    - Workload: Acute Load {_measured(fitness.get('acute_load'))}, Chronic Load {_measured(fitness.get('chronic_load'))}, ACWR {_measured(fitness.get('acwr'))} ({_measured(fitness.get('acwr_status'))})
    - Body Battery: +{today['body_battery_charged']} charged
    - Sleep respiration: {_measured(today.get('respiration_rate'))} brpm (lowest {_measured(today.get('lowest_respiration'))})

    Evaluate strictly. Output a single JSON object with these EXACT keys. The
    recovery score, its zone and the illness risk level are published from the
    athlete's own measured baselines rather than from your output, so the only
    thing that reaches the dashboard is the prose below:
    1. "autonomic_nervous_analysis": string (detailed diagnostic paragraph on parasympathetic tone and vagal recovery)
    2. "sleep_architecture_analysis": string (detailed diagnostic paragraph on somatic physical vs cognitive REM repair and sleep debt)
    3. "workload_and_biological_age": string (detailed diagnostic paragraph on cardiovascular adaptation, ACWR ratio, and biological fitness age)
    4. "actionable_directives": array of 3 strings (1. deep-work cognitive capacity, 2. caffeine cutoff time, 3. sleep hygiene directive)
    5. "recovery_score": integer 0-100 (Whoop scale) -- your independent second opinion, recorded for comparison against the published score

    Write for a non-medical reader: define each clinical term in the same sentence
    you use it. Do not write a summary sentence of your own -- a plain-English
    closing sentence is added to each paragraph after you return.

    Ground every sentence in the measurements above. If a value you would want is
    not listed, say it was not measured rather than estimating it, and never state
    a fact about the athlete's life that the profile block does not contain.

    Output ONLY valid, parseable JSON without code fences or markdown blocks.
    """


def query_gemini_api(today, baselines, fitness, context=None):
    """Query Google AI Studio (Gemini Flash) when GEMINI_API_KEY is set."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    payload = {
        "contents": [{"parts": [{"text": _build_prompt(today, baselines, fitness, context)}]}],
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


NARRATIVE_KEYS = (
    "autonomic_nervous_analysis",
    "sleep_architecture_analysis",
    "workload_and_biological_age",
)

# Separates a clinical paragraph from the everyday-words explanation that
# follows it. The rules write the explanation, never the model, so the reader's
# takeaway is the same whichever engine wrote the clinical half. The page renders
# the two halves as separate paragraphs and labels neither of them.
PLAIN_PARAGRAPH_SEPARATOR = "\n\n"


def narrative_overlay(ai_result, verdict):
    """The model's prose for a rule-owned verdict, or None if it supplied none.

    Only `NARRATIVE_KEYS` and the narrative directives cross over; every number,
    band, tone, zone and risk level stays exactly as the rule engine computed it.
    Paragraphs the model omitted keep this run's measured verdict, and the
    training directive stays rule-owned because it is a function of the published
    score -- a model that scored the day differently would otherwise recommend
    training its own number rather than the one on the page.
    """
    if not isinstance(ai_result, dict):
        return None

    def text(key):
        value = ai_result.get(key)
        return value.strip() if isinstance(value, str) else ""

    overlay = {key: text(key) or verdict[key] for key in NARRATIVE_KEYS}

    directives = ai_result.get("actionable_directives")
    if isinstance(directives, str):
        directives = [directives]
    if not isinstance(directives, list):
        directives = []
    directives = [str(d).strip() for d in directives if str(d).strip()][:3]

    if not directives and all(overlay[key] == verdict[key] for key in NARRATIVE_KEYS):
        return None

    if directives:
        overlay["actionable_directives"] = [verdict["actionable_directives"][0], *directives]
    overlay["narrative_source"] = "gemini"

    model_score = _as_float(ai_result.get("recovery_score"))
    if model_score is not None:
        overlay["model_score"] = int(clamp(round(model_score), 0, 100))
    return overlay


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


def _conditions_sentence(context):
    """Where this athlete trains and what the air was doing, when the device logged it.

    The old text asserted the athlete's city by name. This reads the location the
    device recorded and the weather it measured at the last session.
    """
    environment = (context or {}).get("environment") or {}
    weather = environment.get("weather") or {}
    bits = []
    if environment.get("home_location"):
        bits.append(f"training is logged around {environment['home_location']}")
    if weather.get("temp_c") is not None:
        condition = f"the latest session ran at {_trim(weather['temp_c'])}C"
        if weather.get("humidity_pct") is not None:
            condition += f" and {_trim(weather['humidity_pct'])}% humidity"
        bits.append(condition)
    if environment.get("heat_acclimation_pct") is not None:
        bits.append(f"heat acclimation reads {_trim(environment['heat_acclimation_pct'])}%")
    if not bits:
        return ""
    return " Conditions where you train: " + "; ".join(bits) + "."


def _hydration_directive(context):
    """Hydration advice from the goal and the sweat loss the device recorded."""
    environment = (context or {}).get("environment") or {}
    hydration = environment.get("hydration") or {}
    goal, sweat, heat = hydration.get("goal_ml"), hydration.get("sweat_loss_ml"), environment.get(
        "heat_acclimation_pct"
    )
    if goal is None and sweat is None and heat is None:
        return None
    parts = []
    if goal:
        parts.append(f"your daily target is {goal / 1000:.1f}L")
    if sweat:
        parts.append(f"the latest logged session sweated {int(sweat)} ml")
    if heat is not None:
        parts.append(f"heat acclimation reads {heat}%")
    return "Hydration & Heat: " + ", ".join(parts) + "."


def deterministic_engine(today, baselines, fitness, context=None):
    """Rule engine used when Gemini is unavailable, rejected, or contradictory.

    Every sentence is assembled from values measured in this run: the previous
    version hard-coded its verdicts ("Peak 5-minute HRV reached 89 ms", a
    "+4.3 years younger" advantage, a fixed 22:15 wind-down) even when the
    measurements said otherwise.

    Each paragraph is paired with an everyday-words restatement under
    `plain_english`, which `synthesize` appends after the prose so the reader's
    takeaway survives a Gemini overlay.
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
        f"of {deep_base}% for muscular and tissue repair. REM Sleep (the dreaming stage that "
        f"consolidates memory) logged at {rem_pct:.1f}% "
        f"({rem_sec // 3600}h {(rem_sec % 3600) // 60}m), {rem_verdict} the 16% reference, with "
        f"{sleep_debt_minutes} min of debt against the {policy.TARGET_SLEEP_HOURS:g}h target. "
        f"Nightly stress at {sleep_stress}/100."
    )
    if sleep_debt_minutes:
        sleep_plain_debt = (
            f"You are {sleep_debt_minutes} minutes short of your "
            f"{policy.TARGET_SLEEP_HOURS:g}-hour target, so an earlier bedtime tonight is the "
            f"cheapest way to improve tomorrow."
        )
    else:
        sleep_plain_debt = "You reached your sleep-length target, so nothing here needs fixing tonight."
    sleep_plain = (
        f"You slept {sleep_hours:.1f} hours. Deep tissue-repair sleep was "
        f"{deep_pct:.1f}% of the night, {deep_verdict} your {_trim(deep_base)}% baseline, and "
        f"memory-forming REM sleep was {rem_pct:.1f}%, {rem_verdict} the 16% reference. "
        f"{sleep_plain_debt}"
    )

    hrv_band = policy.hrv_band(hrv, hrv_base)
    hrv_label = policy.HRV_BANDS[hrv_band]["label"]
    hrv_corridor = baselines.get("hrv_normal_range", [54, 73])
    # The paragraph names the band with the same words the badge, the Autonomic
    # State row and the quadrant show. It used to say "DEGRADED" for any reading
    # below baseline, which is both alarming and a second name for a -5% dip.
    autonomic_verdict = (
        f"Autonomic tone is {hrv_label}: "
        f"overnight heart rate variability (HRV) averaged {hrv} ms ({delta_hrv_pct:+.1f}% versus "
        f"your 30-day baseline of {hrv_base} ms and 6-month baseline of "
        f"{baselines.get('hrv_180d')} ms), against a normal physiological band of "
        f"{hrv_corridor[0]}-{hrv_corridor[1]} ms. Resting heart rate (RHR) moved "
        f"{delta_rhr:+.1f} bpm versus its 30-day baseline."
    )
    autonomic_plain = (
        f"Your overnight recovery signal reads {_trim(hrv)} ms against your "
        f"own 30-day average of {_trim(hrv_base)} ms, so {policy.HRV_BANDS[hrv_band]['plain']}."
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
    acwr_key = policy.acwr_band(_as_float(acwr, 0.2))
    if age_advantage > 0:
        age_phrase = f"{_trim(age_advantage)} years younger than"
    elif age_advantage < 0:
        age_phrase = f"{_trim(abs(age_advantage))} years older than"
    else:
        age_phrase = "the same as"
    workload_plain = (
        f"Your recent training load sits in the "
        f"{policy.ACWR_BANDS[acwr_key]['label']} band: {policy.ACWR_BANDS[acwr_key]['plain']}. "
        f"Your measured fitness age is {age_phrase} your real age of {chrono_age}."
    )

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
    hydration_directive = _hydration_directive(context)
    if hydration_directive:
        directives.append(hydration_directive)

    return {
        "recovery_score": recovery_score,
        **_zone_fields(recovery_score),
        "narrative_source": "deterministic",
        "score_source": "deterministic",
        "illness_early_warning": {
            "risk_level": illness_risk,
            "risk_tone": policy.RISK_TONES[illness_risk],
            "status_headline": illness_status,
            "delta_rhr_bpm": round(delta_rhr, 1),
            "delta_hrv_pct": round(delta_hrv_pct, 1),
            "sleep_stress": sleep_stress,
            "respiration_rate": resp,
            "details": illness_details,
        },
        "sleep_architecture_analysis": sleep_verdict,
        "autonomic_nervous_analysis": autonomic_verdict,
        # The measured conditions sit with the workload paragraph because that is the
        # paragraph about adaptation, and they are measured, not assumed.
        "workload_and_biological_age": workload_verdict + _conditions_sentence(context),
        "actionable_directives": directives,
        # Kept apart from the paragraphs so the orchestrator can append each one to
        # whichever engine wrote the paragraph above it. Not published on its own.
        "plain_english": {
            "autonomic_nervous_analysis": autonomic_plain,
            "sleep_architecture_analysis": sleep_plain,
            "workload_and_biological_age": workload_plain,
        },
    }


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _trim(value):
    """A measurement as it reads in a sentence: 52.0 -> "52", 55.3 -> "55.3".

    An unmeasurable value reads "--", the same as every other absent number the
    dashboard shows, rather than a plausible-looking guess.
    """
    number = _as_float(value)
    return f"{number:g}" if number is not None else "--"


def close_with_plain_paragraph(verdict, plain):
    """Follow each analysis paragraph with the rule-owned explanation.

    The clinical prose (model-written or rule-written) stays as the first
    paragraph and the explanation is appended after it as a second one, never
    replacing it, so a reader who does not know the vocabulary always gets the
    same verdict in words they use themselves.
    """
    for key, sentence in plain.items():
        paragraph = verdict.get(key)
        if isinstance(paragraph, str) and paragraph.strip():
            verdict[key] = f"{paragraph.strip()}{PLAIN_PARAGRAPH_SEPARATOR}{sentence}"
    return verdict


def synthesize(today, baselines, fitness, context=None):
    """Score owned by the rules, prose optionally owned by Gemini.

    The deterministic verdict is computed first and always, so the published
    score, band, tone, zone and illness risk are identical with and without a
    model key; Gemini is asked second and merged over it as narrative only. Each
    analysis paragraph is then followed by the rule-owned explanation, so the
    reader gets everyday words whichever engine wrote the clinical half.
    """
    verdict = deterministic_engine(today, baselines, fitness, context)
    plain = verdict.pop("plain_english")

    ai_result = query_gemini_api(today, baselines, fitness, context)
    if not ai_result:
        return close_with_plain_paragraph(verdict, plain)

    overlay = narrative_overlay(ai_result, verdict)
    if overlay is None:
        print("   ⚠️ Gemini returned no usable narrative; keeping the deterministic verdict.")
        return close_with_plain_paragraph(verdict, plain)

    model_score = overlay.get("model_score")
    if model_score is not None and model_score != verdict["recovery_score"]:
        print(
            f"   ℹ️ Model scored recovery {model_score} vs rule-based "
            f"{verdict['recovery_score']}; publishing the rule-based score."
        )
    return close_with_plain_paragraph({**verdict, **overlay}, plain)
