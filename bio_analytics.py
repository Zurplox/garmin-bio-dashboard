"""
Pure analytics: numbers in, numbers out.

Nothing here performs I/O, reads the environment, or knows about provenance --
the same functions run against live Garmin data, cached data and test fixtures.
Fetching lives in `garmin_source`, the meaning of every number lives in
`bio_policy`, and orchestration lives in `sync.py`. This module only computes.

The readiness and injury-risk models used to live in the browser's render loop;
they are ported here unchanged so the engine owns every score and the dashboard
owns only its presentation.
"""

import datetime
import math
from statistics import median

import bio_correlate
import bio_policy as policy


# --- numeric helpers -------------------------------------------------------

def clamp(value, low, high):
    return max(low, min(high, value))


def percent_split(counts):
    """Turn raw bucket counts into integer percentages that total exactly 100."""
    total = sum(counts.values())
    if total <= 0:
        return {k: 0 for k in counts}
    exact = {k: counts[k] / total * 100 for k in counts}
    floors = {k: int(exact[k]) for k in counts}
    remainder = 100 - sum(floors.values())
    for key in sorted(counts, key=lambda k: exact[k] - floors[k], reverse=True):
        if remainder <= 0:
            break
        floors[key] += 1
        remainder -= 1
    return floors


def _truthy(value, default):
    """Javascript `||` semantics: None and numeric zero both fall through."""
    if value is None:
        return default
    try:
        if float(value) == 0:
            return default
    except (TypeError, ValueError):
        pass
    return value


def _nullish(value, default):
    """Javascript `??` semantics: only None falls through."""
    return default if value is None else value


# --- time ------------------------------------------------------------------

def format_clock_12h(minutes):
    """Minutes-after-midnight -> 12-hour clock string, e.g. 1303 -> "9:43 PM".

    The previous implementation printed 24-hour numbers with a "PM" suffix,
    producing values such as "22:03 PM SGT".
    """
    minutes = int(round(minutes)) % (24 * 60)
    hour, minute = divmod(minutes, 60)
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def bedtime_minutes_from_record(dto):
    """Garmin sleep onset (epoch ms, UTC) -> Singapore evening-anchored minutes.

    Onsets after midnight are mapped past 1440 (00:30 -> 1470) so that
    averaging and variance calculations treat 23:30 and 00:30 as neighbours
    instead of opposites.
    """
    ts = dto.get("sleepStartTimestampGMT")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    utc_minutes = int(ts // 60000) % (24 * 60)
    local = (utc_minutes + policy.SGT_OFFSET_MINUTES) % (24 * 60)
    if local < 12 * 60:  # anything before noon belongs to the previous evening
        local += 24 * 60
    return local


# --- 24-hour stress --------------------------------------------------------

def stress_distribution_from_samples(samples):
    """Bucket Garmin stress samples into the four autonomic states.

    Garmin samples stress roughly every 3 minutes; values below zero are
    sentinels (-1 = no reading, -2 = activity / off-wrist) and must not be
    bucketed. Returns the distribution (or None when there is too little
    telemetry to be meaningful), the average, and the usable sample count.
    """
    buckets = {"rest": 0, "low": 0, "med": 0, "high": 0}
    levels = []
    for sample in samples or []:
        if not isinstance(sample, (list, tuple)) or len(sample) < 2:
            continue
        level = sample[1]
        if not isinstance(level, (int, float)) or isinstance(level, bool) or level < 0:
            continue
        levels.append(float(level))
        if level <= policy.STRESS_REST_MAX:
            buckets["rest"] += 1
        elif level <= policy.STRESS_LOW_MAX:
            buckets["low"] += 1
        elif level <= policy.STRESS_MED_MAX:
            buckets["med"] += 1
        else:
            buckets["high"] += 1

    if len(levels) < 20:
        return None, None, len(levels)

    pct = percent_split(buckets)
    distribution = {
        "rest_pct": pct["rest"],
        "low_pct": pct["low"],
        "med_pct": pct["med"],
        "high_pct": pct["high"],
        "samples": len(levels),
        "band": policy.stress_band(round(sum(levels) / len(levels), 1)),
    }
    return distribution, round(sum(levels) / len(levels), 1), len(levels)


# --- series windows --------------------------------------------------------
# Positional windows ("the last 30 days") are decided here, not by callers, so
# no module has to remember which order a list arrived in.

def latest_record(records, date_key):
    """The most recent record by date, whatever order the list is in."""
    dated = [r for r in records if r.get(date_key)]
    return max(dated, key=lambda r: r[date_key]) if dated else {}


def _by(records, date_key):
    return sorted(records, key=lambda r: r.get(date_key) or "")


def calculate_baselines(all_rhr, all_hrv, sleep_history):
    """Multi-year, 6-month, 30-day and 7-day physiological baselines."""
    # Defensive ordering: every "recent N days" window below slices these lists,
    # so upstream ordering must never be trusted.
    all_rhr = _by(all_rhr, "calendarDate")
    all_hrv = _by(all_hrv, "calendarDate")
    sleep_history = _by(sleep_history, "date")

    # RHR
    rhr_values_all = [r["value"] for r in all_rhr if r.get("value")]
    rhr_all_time = round(sum(rhr_values_all) / len(rhr_values_all), 1) if rhr_values_all else 50.0

    rhr_by_year = {}
    for r in all_rhr:
        yr = r["calendarDate"][:4]
        if yr not in rhr_by_year:
            rhr_by_year[yr] = []
        if r.get("value"):
            rhr_by_year[yr].append(r["value"])

    rhr_year_avg = {
        yr: round(sum(vals) / len(vals), 1) for yr, vals in sorted(rhr_by_year.items()) if vals
    }

    rhr_by_month = {}
    for r in all_rhr:
        ym = r["calendarDate"][:7]
        if ym not in rhr_by_month:
            rhr_by_month[ym] = []
        if r.get("value"):
            rhr_by_month[ym].append(r["value"])

    # `year` travels with `month` because the chart labels each point with both.
    rhr_multi_year = [
        {"month": ym, "year": ym[:4], "rhr": round(sum(vals) / len(vals), 1)}
        for ym, vals in sorted(rhr_by_month.items()) if vals
    ]

    recent_180_rhr = [r["value"] for r in all_rhr[-180:] if r.get("value")]
    rhr_180d = round(sum(recent_180_rhr) / len(recent_180_rhr), 1) if recent_180_rhr else rhr_all_time

    recent_30_rhr = [r["value"] for r in all_rhr[-30:] if r.get("value")]
    rhr_30d = round(sum(recent_30_rhr) / len(recent_30_rhr), 1) if recent_30_rhr else rhr_all_time

    recent_7_rhr = [r["value"] for r in all_rhr[-7:] if r.get("value")]
    rhr_7d = round(sum(recent_7_rhr) / len(recent_7_rhr), 1) if recent_7_rhr else rhr_30d

    # HRV
    hrv_last_night_vals = [h["lastNightAvg"] for h in all_hrv if h.get("lastNightAvg")]
    hrv_all_time = round(sum(hrv_last_night_vals) / len(hrv_last_night_vals), 1) if hrv_last_night_vals else 56.0

    recent_180_hrv = [h["lastNightAvg"] for h in all_hrv[-180:] if h.get("lastNightAvg")]
    hrv_180d = round(sum(recent_180_hrv) / len(recent_180_hrv), 1) if recent_180_hrv else hrv_all_time

    recent_30_hrv = [h["lastNightAvg"] for h in all_hrv[-30:] if h.get("lastNightAvg")]
    hrv_30d = round(sum(recent_30_hrv) / len(recent_30_hrv), 1) if recent_30_hrv else hrv_all_time

    recent_7_hrv = [h["lastNightAvg"] for h in all_hrv[-7:] if h.get("lastNightAvg")]
    hrv_7d = round(sum(recent_7_hrv) / len(recent_7_hrv), 1) if recent_7_hrv else hrv_30d

    latest_hrv = all_hrv[-1] if all_hrv else {}
    hrv_baseline_low = latest_hrv.get("baseline", {}).get("balancedLow", 54)
    hrv_baseline_high = latest_hrv.get("baseline", {}).get("balancedUpper", 73)

    # Sleep baselines (6-month vs 30-day)
    recent_30_sleep = sleep_history[-30:] if len(sleep_history) >= 30 else sleep_history
    scores_30 = [s["score"] for s in recent_30_sleep if s.get("score")]
    avg_score_30d = round(sum(scores_30) / len(scores_30), 1) if scores_30 else 80.0

    total_secs_30 = [s["total_seconds"] for s in recent_30_sleep if s.get("total_seconds")]
    avg_duration_30 = round((sum(total_secs_30) / len(total_secs_30)) / 3600.0, 1) if total_secs_30 else 7.0

    deep_pcts_30 = [
        (s["deep_seconds"] / s["total_seconds"] * 100.0)
        for s in recent_30_sleep if s.get("total_seconds") and s.get("deep_seconds")
    ]
    avg_deep_30 = round(sum(deep_pcts_30) / len(deep_pcts_30), 1) if deep_pcts_30 else 22.0

    rem_pcts_30 = [
        (s["rem_seconds"] / s["total_seconds"] * 100.0)
        for s in recent_30_sleep if s.get("total_seconds") and s.get("rem_seconds")
    ]
    avg_rem_30 = round(sum(rem_pcts_30) / len(rem_pcts_30), 1) if rem_pcts_30 else 16.0

    # 180-day sleep baselines
    scores_all = [s["score"] for s in sleep_history if s.get("score")]
    avg_score_180d = round(sum(scores_all) / len(scores_all), 1) if scores_all else avg_score_30d

    total_secs_all = [s["total_seconds"] for s in sleep_history if s.get("total_seconds")]
    avg_duration_180d = round((sum(total_secs_all) / len(total_secs_all)) / 3600.0, 1) if total_secs_all else avg_duration_30

    deep_pcts_all = [
        (s["deep_seconds"] / s["total_seconds"] * 100.0)
        for s in sleep_history if s.get("total_seconds") and s.get("deep_seconds")
    ]
    avg_deep_180d = round(sum(deep_pcts_all) / len(deep_pcts_all), 1) if deep_pcts_all else avg_deep_30

    resp_vals = [s["avg_respiration"] for s in sleep_history if s.get("avg_respiration")]
    avg_resp_30d = round(sum(resp_vals[-30:]) / len(resp_vals[-30:]), 1) if len(resp_vals) >= 30 else 13.0

    baselines = {
        "rhr_7d": rhr_7d,
        "rhr_30d": rhr_30d,
        "rhr_180d": rhr_180d,
        "rhr_all_time": rhr_all_time,
        "rhr_yearly": rhr_year_avg,
        "rhr_total_days": len(rhr_values_all),
        "rhr_months_tracked": len(rhr_multi_year),
        "hrv_7d": hrv_7d,
        "hrv_30d": hrv_30d,
        "hrv_180d": hrv_180d,
        "hrv_all_time": hrv_all_time,
        "hrv_normal_range": [hrv_baseline_low, hrv_baseline_high],
        "sleep_score_30d": avg_score_30d,
        "sleep_score_180d": avg_score_180d,
        "sleep_duration_avg_30d_hours": avg_duration_30,
        "sleep_duration_avg_180d_hours": avg_duration_180d,
        "deep_sleep_pct_30d": avg_deep_30,
        "deep_sleep_pct_180d": avg_deep_180d,
        "rem_sleep_pct_30d": avg_rem_30,
        "respiration_avg_30d": avg_resp_30d,
    }

    return baselines, rhr_multi_year


def compute_circadian_architecture(sleep_history):
    """Derive circadian metrics from real sleep-onset telemetry.

    Produces the athlete's median sleep onset, its night-to-night regularity, a
    melatonin gate (a 30-minute window ending 15 minutes before median onset)
    and a consistency-based alignment percentage. All of these were previously
    hard-coded constants ("22:15 PM - 22:45 PM SGT", 88%).
    """
    onsets = [
        s["bedtime_minutes"]
        for s in _by(sleep_history, "date")
        if isinstance(s.get("bedtime_minutes"), (int, float))
    ][-21:]

    if len(onsets) < 5:
        return {
            "available": False,
            "nights_used": len(onsets),
            "onset_median_minutes": None,
            "onset_median_clock": None,
            "onset_std_dev_minutes": None,
            "melatonin_window": None,
            "melatonin_window_minutes": None,
            "alignment_pct": None,
        }

    onset_median = median(onsets)
    mean_onset = sum(onsets) / len(onsets)
    std_dev = (sum((o - mean_onset) ** 2 for o in onsets) / len(onsets)) ** 0.5
    # 100% alignment == a completely regular bedtime; every 2 minutes of
    # night-to-night spread costs one percentage point, floored at 0.
    alignment = clamp(round(100 - std_dev / 2.0), 0, 100)

    return {
        "available": True,
        "nights_used": len(onsets),
        "onset_median_minutes": round(onset_median),
        "onset_median_clock": format_clock_12h(onset_median),
        "onset_std_dev_minutes": round(std_dev),
        "melatonin_window": (
            f"{format_clock_12h(onset_median - 45)} — "
            f"{format_clock_12h(onset_median - 15)} SGT"
        ),
        "melatonin_window_minutes": [round(onset_median - 45), round(onset_median - 15)],
        "alignment_pct": alignment,
    }


# --- today -----------------------------------------------------------------

def build_today_snapshot(today_str, sleep_record, hrv_record, recent_rhr, body_battery, daily_summary, stress):
    """Assemble today's flat snapshot from already-fetched inputs.

    `body_battery`, `daily_summary` and `stress` come from `garmin_source`,
    which substitutes documented placeholders when an endpoint fails and records
    that fallback with the provenance tracker.
    """
    sleep_record = sleep_record or {}
    hrv_record = hrv_record or {}
    latest_rhr_record = max(recent_rhr, key=lambda r: r.get("calendarDate") or "") if recent_rhr else None
    rhr_today = latest_rhr_record["value"] if latest_rhr_record is not None else 51.0
    rhr_tier = policy.rhr_tier(rhr_today)
    # Garmin's own status word. It is context for the HRV panel only: the band it
    # sits in against the 30-day baseline is the single meaning the page renders.
    hrv_watch_status = hrv_record.get("status", "BALANCED")

    stress_avg = daily_summary.get("stress_avg", 15)
    if stress.get("average") is not None:
        stress_avg = stress["average"]

    return {
        "date": today_str,
        "sleep_score": sleep_record.get("score", 78),
        "sleep_time_seconds": sleep_record.get("total_seconds", 23700),
        "deep_sleep_seconds": sleep_record.get("deep_seconds", 5580),
        "rem_sleep_seconds": sleep_record.get("rem_seconds", 3780),
        "light_sleep_seconds": sleep_record.get("light_seconds", 14340),
        "awake_sleep_seconds": sleep_record.get("awake_seconds", 0),
        "sleep_stress": sleep_record.get("avg_stress", 16.0),
        "respiration_rate": sleep_record.get("avg_respiration", 13.0),
        "lowest_respiration": sleep_record.get("lowest_respiration", 9.0),
        "hrv_last_night": hrv_record.get("lastNightAvg", 60),
        "hrv_weekly_avg": hrv_record.get("weeklyAvg", 57),
        "hrv_status": hrv_watch_status,
        "hrv_baseline_low": hrv_record.get("baseline", {}).get("balancedLow", 54),
        "hrv_baseline_high": hrv_record.get("baseline", {}).get("balancedUpper", 73),
        "rhr": rhr_today,
        # The tier arrives resolved so the badge beside the number cannot drift
        # from the measurement the way a fixed label did.
        "rhr_tier": rhr_tier,
        "rhr_tier_label": policy.RHR_TIERS[rhr_tier]["badge"],
        "rhr_tier_tone": policy.RHR_TIERS[rhr_tier]["tone"],
        "body_battery_charged": body_battery.get("charged", 38),
        "body_battery_drained": body_battery.get("drained", 0),
        "stress_avg": stress_avg,
        "stress_distribution": stress.get("distribution"),
        "steps": daily_summary.get("steps", 47),
    }


# --- derived scores --------------------------------------------------------

def calculate_day_strain(today_str, steps, stress_avg, activities):
    """Day strain on the 0.0-21.0 scale (modelled, not a Garmin field).

    Built only from channels this pipeline actually receives:
      * ambulatory volume - steps, saturating at STEPS_SATURATION
      * training sessions - duration x aerobic training effect
      * all-day stress    - Garmin stress average
    They combine into a dimensionless load index L, mapped through
    21 * (1 - e^-L) so the result is monotonic and saturates at the scale max.
    The previous version clamped at a hard floor of 3.8, so every light day
    reported the identical "3.8" and the gauge could never read lower.
    """
    session_load = 0.0
    if today_str:
        for act in activities[:8]:
            if str(act.get("startTimeLocal", "")).startswith(today_str):
                dur_min = act.get("duration_min") or 0
                aerobic = act.get("aerobicTrainingEffect") or 0
                session_load += (dur_min / 60.0) * (float(aerobic) / 3.0)

    load_index = (
        (steps / policy.STEPS_SATURATION) * 0.5
        + session_load
        + (stress_avg / 100.0) * 0.3
    )
    return round(clamp(policy.DAY_STRAIN_SCALE_MAX * (1.0 - math.exp(-load_index)), 0.0, policy.DAY_STRAIN_SCALE_MAX), 1)


def calculate_sleep_need(day_strain, today, sleep_history):
    """Sleep need, debt paydown and the reverse-engineered bedtime."""
    recent_7_sleep = sleep_history[-7:] if len(sleep_history) >= 7 else sleep_history
    raw_7d_debt = 0
    for s in recent_7_sleep:
        hrs = s.get("total_seconds", 0) / 3600.0
        if hrs < policy.TARGET_SLEEP_HOURS:
            raw_7d_debt += int((policy.TARGET_SLEEP_HOURS - hrs) * 60)

    nightly_debt_repayment = min(
        max(raw_7d_debt // policy.SLEEP_DEBT_DIVISOR, policy.SLEEP_DEBT_FLOOR_MINUTES),
        policy.SLEEP_DEBT_CEILING_MINUTES,
    )
    strain_sleep_need_min = int(day_strain * policy.STRAIN_TO_SLEEP_MINUTES)
    total_sleep_need_min = (
        policy.BASELINE_SLEEP_NEED_MINUTES + nightly_debt_repayment + strain_sleep_need_min
    )
    total_sleep_need_hrs = round(total_sleep_need_min / 60.0, 1)

    actual_sleep_sec = today.get("sleep_time_seconds", 23700)
    sleep_perf_pct = int(min(max((actual_sleep_sec / (total_sleep_need_min * 60.0)) * 100, 10), 100))
    awake_sec = today.get("awake_sleep_seconds", 0)
    time_in_bed_sec = actual_sleep_sec + awake_sec
    sleep_efficiency_pct = int(round((actual_sleep_sec / time_in_bed_sec * 100) if time_in_bed_sec > 0 else 96))

    durations = [s.get("total_seconds", 24000) / 3600.0 for s in sleep_history[-14:]]
    if durations:
        mean_dur = sum(durations) / len(durations)
        variance = sum((x - mean_dur) ** 2 for x in durations) / len(durations)
        std_dev_hrs = variance ** 0.5
        sleep_consistency_pct = int(max(min(100 - (std_dev_hrs / 2.0) * 50, 98), 60))
    else:
        sleep_consistency_pct = 85

    # Target wake is fixed; bedtime is reverse-engineered from total sleep need
    # plus a sleep-onset allowance, rendered as a 12-hour clock.
    target_bedtime_min = (
        policy.TARGET_WAKE_MINUTES
        - (total_sleep_need_min + policy.SLEEP_ONSET_ALLOWANCE_MINUTES)
    ) % (24 * 60)

    return {
        "sleep_performance_pct": sleep_perf_pct,
        "sleep_efficiency_pct": sleep_efficiency_pct,
        "sleep_consistency_pct": sleep_consistency_pct,
        "baseline_sleep_need_hours": round(policy.BASELINE_SLEEP_NEED_MINUTES / 60.0, 1),
        "sleep_debt_minutes": nightly_debt_repayment,
        "accumulated_7d_debt_hours": round(raw_7d_debt / 60.0, 1),
        "strain_sleep_need_minutes": strain_sleep_need_min,
        "total_sleep_need_hours": total_sleep_need_hrs,
        "total_sleep_need_formatted": f"{total_sleep_need_min // 60}h {total_sleep_need_min % 60}m",
        "recommended_bedtime": f"{format_clock_12h(target_bedtime_min)} SGT",
        "recommended_bedtime_minutes": target_bedtime_min,
        "recommended_wake_time": f"{format_clock_12h(policy.TARGET_WAKE_MINUTES)} SGT",
        "recommended_wake_minutes": policy.TARGET_WAKE_MINUTES,
    }


def calculate_whoop_metrics(today, activities, sleep_history, intelligence):
    """Strain, target strain band and the sleep-need engine."""
    recovery_score = _truthy(intelligence.get("recovery_score"), 76)
    steps = today.get("steps", 50)
    stress_avg = today.get("stress_avg", 15)

    day_strain = calculate_day_strain(today.get("date", ""), steps, stress_avg, activities)
    target = policy.strain_target(recovery_score)

    return {
        "day_strain": day_strain,
        "target_strain_min": target["min"],
        "target_strain_max": target["max"],
        "strain_zone": target["zone"],
        "strain_advice": target["advice"],
        **calculate_sleep_need(day_strain, today, sleep_history),
    }


def calculate_fitbit_metrics(today, baselines, fitness):
    """Fitbit-premium style readiness score and the five health pillars.

    Each pillar's status and colour are derived from the value against its own
    range rather than being a fixed "all good" verdict, which is what they used
    to be.
    """
    acute_load = fitness.get("acute_load", 44)
    hrv_val = today.get("hrv_last_night", 60)
    hrv_base = baselines.get("hrv_30d", 55.3)
    hrv_delta = ((hrv_val - hrv_base) / hrv_base * 100.0) if hrv_base else 0.0
    hrv_score = min(max(int((hrv_val / hrv_base) * 80), 30), 100)
    sleep_score = today.get("sleep_score", 78)
    fatigue_score = 90 if acute_load < 80 else max(90 - int((acute_load - 80) * 0.3), 30)
    readiness_score = int(round((hrv_score * 0.4) + (sleep_score * 0.4) + (fatigue_score * 0.2)))

    respiration = today.get("respiration_rate", 13.0)
    sleep_stress = today.get("sleep_stress", 16.0)
    rhr = round(today.get("rhr", 51.0), 1)
    rhr_delta = rhr - baselines.get("rhr_30d", 49.8)
    hrv_range = baselines.get("hrv_normal_range", [54, 73])
    fitness_age = fitness.get("fitness_age", 24.7)
    chronological_age = fitness.get("chronological_age", 29)
    age_advantage = chronological_age - fitness_age

    if 11.5 <= respiration <= 14.5:
        breathing_status, breathing_tone = "In Range", "emerald"
    elif respiration <= 16.0:
        breathing_status, breathing_tone = "Slightly Elevated", "amber"
    else:
        breathing_status, breathing_tone = "Elevated", "rose"

    # One band, one label, one tone: the KPI badge and the Autonomic State row read
    # these same three fields, so the pillar is where HRV's meaning is decided.
    hrv_band = policy.hrv_band(hrv_val, hrv_base)
    hrv_tone = policy.HRV_BANDS[hrv_band]["tone"]
    hrv_status = policy.HRV_BANDS[hrv_band]["label"]

    if sleep_stress <= 25:
        stress_status, stress_tone = "Restorative", "emerald"
    elif sleep_stress <= 35:
        stress_status, stress_tone = "Elevated", "amber"
    else:
        stress_status, stress_tone = "High", "rose"

    if rhr_delta <= 1.0:
        rhr_status, rhr_tone = "Normal Rhythm", "emerald"
    elif rhr_delta <= 3.0:
        rhr_status, rhr_tone = "Slightly Elevated", "amber"
    else:
        rhr_status, rhr_tone = "Elevated — possible systemic load", "rose"

    return {
        "daily_readiness_score": readiness_score,
        "readiness_category": "Good (Primed for Activity)" if readiness_score >= policy.READINESS_PRIME_MIN else "Moderate",
        "hrv_component_score": hrv_score,
        "sleep_component_score": sleep_score,
        "fatigue_component_score": fatigue_score,
        "health_metrics_5_pillars": [
            {
                "name": "Breathing Rate",
                "key": "breathing_rate",
                "value": respiration,
                "unit": "brpm",
                "baseline": baselines.get("respiration_avg_30d", 12.5),
                "range_min": 11.5,
                "range_max": 14.5,
                "status": breathing_status,
                "status_color": breathing_tone,
                "description": "Nocturnal breathing rate against the normal 11.5-14.5 brpm band.",
            },
            {
                "name": "Heart Rate Variability",
                "key": "hrv",
                "value": hrv_val,
                "unit": "ms",
                "baseline": hrv_base,
                "range_min": hrv_range[0],
                "range_max": hrv_range[1],
                "status": hrv_status,
                "status_color": hrv_tone,
                "description": f"{hrv_delta:+.1f}% versus the 30-day baseline of {hrv_base} ms.",
            },
            {
                "name": "Sleep Autonomic Stress",
                "key": "sleep_stress",
                "value": sleep_stress,
                "unit": "/100",
                "baseline": 20.0,
                "range_min": 10.0,
                "range_max": 25.0,
                "status": stress_status,
                "status_color": stress_tone,
                "description": "Autonomic disruption during slow-wave and REM cycles.",
            },
            {
                "name": "Resting Heart Rate",
                "key": "rhr",
                "value": rhr,
                "unit": "bpm",
                "baseline": baselines.get("rhr_30d", 49.8),
                "range_min": 48.0,
                "range_max": 54.0,
                "status": rhr_status,
                "status_color": rhr_tone,
                "description": f"{rhr_delta:+.1f} bpm versus the 30-day baseline.",
            },
            {
                "name": "Cardio Fitness (Fitness Age)",
                "key": "fitness_age",
                "value": fitness_age,
                "unit": "yrs",
                "baseline": chronological_age,
                "range_min": fitness.get("achievable_fitness_age", 21.1),
                "range_max": chronological_age,
                "status": (
                    f"Elite ({age_advantage:.1f} yrs younger)"
                    if age_advantage > 0
                    else f"{age_advantage:+.1f} yrs versus chronological age"
                ),
                "status_color": "cyan" if age_advantage > 0 else "amber",
                "description": "Aerobic and recovery capacity against chronological age.",
            },
        ],
    }


def build_garmin_signature(today, circadian):
    """Garmin-signature block: Body Battery, stress architecture, circadian gate.

    The stress distribution is passed through as measured when telemetry was
    available. The fallback derives a coarse shape from the daily average alone
    and labels itself "estimated" so the UI never presents it as 24-hour
    telemetry.
    """
    stress_avg = today.get("stress_avg", 15)
    measured = today.get("stress_distribution")

    if measured:
        distribution = {
            "rest_pct": measured.get("rest_pct", 0),
            "low_pct": measured.get("low_pct", 0),
            "med_pct": measured.get("med_pct", 0),
            "high_pct": measured.get("high_pct", 0),
            "samples": measured.get("samples"),
            "band": policy.stress_band(stress_avg),
        }
        source = "measured"
    else:
        if stress_avg <= 20:
            rest_pct, low_pct, med_pct, high_pct = 75, 18, 5, 2
        elif stress_avg <= 35:
            rest_pct, low_pct, med_pct, high_pct = 55, 28, 12, 5
        else:
            rest_pct, low_pct, med_pct, high_pct = 35, 35, 20, 10
        distribution = {
            "rest_pct": rest_pct,
            "low_pct": low_pct,
            "med_pct": med_pct,
            "high_pct": high_pct,
            "samples": None,
            "band": policy.stress_band(stress_avg),
        }
        source = "estimated"

    charged = today.get("body_battery_charged", 38)
    battery_band = policy.body_battery_band(charged)

    return {
        "body_battery_charged": charged,
        "body_battery_drained": today.get("body_battery_drained", 0),
        "body_battery_band": battery_band,
        "body_battery_badge": policy.BODY_BATTERY_BANDS[battery_band]["badge"],
        "body_battery_tone": policy.BODY_BATTERY_BANDS[battery_band]["tone"],
        "stress_avg": stress_avg,
        "stress_band": distribution["band"],
        "stress_label": policy.STRESS_BANDS[distribution["band"]]["label"],
        "stress_tone": policy.STRESS_BANDS[distribution["band"]]["tone"],
        "stress_distribution": distribution,
        "stress_distribution_source": source,
        "circadian": {
            "sleep_cycles_completed": round(today.get("sleep_time_seconds", 23700) / (policy.SLEEP_CYCLE_MINUTES * 60), 1),
            "available": bool((circadian or {}).get("available")),
            "optimal_melatonin_window": (circadian or {}).get("melatonin_window"),
            "melatonin_window_minutes": (circadian or {}).get("melatonin_window_minutes"),
            "circadian_alignment_pct": (circadian or {}).get("alignment_pct"),
            "onset_median_clock": (circadian or {}).get("onset_median_clock"),
            "onset_std_dev_minutes": (circadian or {}).get("onset_std_dev_minutes"),
            "onsets_analysed": (circadian or {}).get("nights_used", 0),
        },
    }


# --- additional measured signal groups -------------------------------------

def _clock_from_local_timestamp(stamp):
    """"2026-09-21T20:31:00.0" -> "8:31 PM SGT". None when unparseable."""
    if not isinstance(stamp, str) or "T" not in stamp:
        return None
    time_part = stamp.split("T", 1)[1][:5]
    try:
        hour, minute = (int(p) for p in time_part.split(":"))
    except ValueError:
        return None
    return f"{format_clock_12h(hour * 60 + minute)} SGT"


def build_oxygen_signal(days):
    """Blood oxygen from the device's own readings, with its coverage stated.

    This watch records Pulse Ox opportunistically -- a spot reading most days and
    an overnight summary on some nights -- so the signal publishes how many days
    in the window actually carried a reading. Presenting one number without that
    coverage line would make a spot check look like a nightly average.
    """
    days = [d for d in (days or []) if d.get("latest") is not None or d.get("sleep_average") is not None]
    days.sort(key=lambda d: d.get("date") or "")
    window = policy.SPO2_LOOKBACK_DAYS

    latest = days[-1] if days else None
    sleep_averages = [d["sleep_average"] for d in days if d.get("sleep_average") is not None]
    lowest_values = [(d["lowest"], d["date"]) for d in days if d.get("lowest") is not None]
    sleep_average = round(sum(sleep_averages) / len(sleep_averages), 1) if sleep_averages else None
    lowest, lowest_date = min(lowest_values) if lowest_values else (None, None)

    band = policy.spo2_band(sleep_average if sleep_average is not None else (latest or {}).get("latest"))
    coverage_pct = round(len(days) / window * 100) if window else 0

    return {
        "available": bool(days),
        "latest": (latest or {}).get("latest"),
        "latest_date": (latest or {}).get("date"),
        "latest_clock": _clock_from_local_timestamp((latest or {}).get("latest_time_local")),
        "sleep_average": sleep_average,
        "sleep_nights": len(sleep_averages),
        "lowest": lowest,
        "lowest_date": lowest_date,
        "dip_flagged": bool(lowest is not None and lowest <= policy.SPO2_DIP_MIN),
        "days_recorded": len(days),
        "window_days": window,
        "coverage_pct": coverage_pct,
        "band": band,
        "band_label": policy.SPO2_BANDS[band]["label"] if band else None,
        "band_tone": policy.SPO2_BANDS[band]["tone"] if band else "slate",
        "plain": policy.SPO2_BANDS[band]["plain"] if band else None,
        "series": [{"date": d["date"], "value": d.get("latest") or d.get("sleep_average")} for d in days],
    }


def build_environment_signal(location_days, weather, heat_acclimation_pct, hydration):
    """Where the training happened, what the air was doing, and heat adaptation.

    Locations come from the device's own activity records, so "home" is the
    location it logged most often -- not an assumption about where the athlete
    lives -- and "away" is any other logged location.
    """
    counts, spans = {}, {}
    for entry in location_days or []:
        name = entry.get("location")
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        date = entry.get("date")
        if date:
            first, last = spans.get(name, (date, date))
            spans[name] = (min(first, date), max(last, date))

    home = bio_correlate.home_location(location_days)
    away_stays = [
        {"location": name, "sessions": sessions, "first": spans[name][0], "last": spans[name][1]}
        for name, sessions in sorted(counts.items(), key=lambda kv: -kv[1])
        if name != home
    ]
    last_location = (location_days or [{}])[-1].get("location") if location_days else None

    heat_band = policy.heat_band(heat_acclimation_pct)
    hydration = hydration or {}
    goal_ml = hydration.get("goal_ml")
    intake_ml = hydration.get("intake_ml")

    return {
        "available": bool(counts),
        "home_location": home,
        "last_location": last_location,
        "locations": [
            {"name": name, "sessions": sessions, "first": spans[name][0], "last": spans[name][1]}
            for name, sessions in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
        "away_stays": away_stays,
        "away_sessions": sum(s["sessions"] for s in away_stays),
        "weather": weather or {},
        "hot_session": bool(
            weather
            and weather.get("temp_c") is not None
            and weather["temp_c"] >= policy.HOT_SESSION_TEMP_C
        ),
        "humid_session": bool(
            weather
            and weather.get("humidity_pct") is not None
            and weather["humidity_pct"] >= policy.HUMID_SESSION_PCT
        ),
        "heat_acclimation_pct": heat_acclimation_pct,
        "heat_band": heat_band,
        "heat_label": policy.HEAT_BANDS[heat_band]["label"] if heat_band else None,
        "heat_tone": policy.HEAT_BANDS[heat_band]["tone"] if heat_band else "slate",
        "heat_plain": policy.HEAT_BANDS[heat_band]["plain"] if heat_band else None,
        "hydration": {
            "goal_ml": goal_ml,
            "intake_ml": intake_ml,
            "sweat_loss_ml": hydration.get("sweat_loss_ml"),
            "intake_pct": (
                round(intake_ml / goal_ml * 100) if goal_ml and intake_ml is not None else None
            ),
        },
    }


def build_capacity_signal(profile, race_predictions, intensity, daily_activity, today_str):
    """Training capacity from the athlete's own profile and forecasts.

    Chronological age, height, weight and BMI are read from the account's profile
    rather than assumed, so the only personal facts on the page are measured ones.
    """
    profile = profile or {}
    height_cm = profile.get("height_cm")
    weight_kg = profile.get("weight_kg")
    bmi = None
    if height_cm and weight_kg:
        bmi = round(weight_kg / ((height_cm / 100.0) ** 2), 1)
    bmi_key = policy.bmi_band(bmi)

    weekly_minutes = (intensity or {}).get("weekly_total")
    intensity_key = policy.intensity_band(weekly_minutes)
    races = []
    for label, key in (("5K", "time5k"), ("10K", "time10k"), ("Half", "time_half"), ("Marathon", "time_marathon")):
        seconds = (race_predictions or {}).get(key)
        if seconds:
            hours, remainder = divmod(int(seconds), 3600)
            minutes, secs = divmod(remainder, 60)
            races.append(
                {
                    "label": label,
                    "seconds": int(seconds),
                    "formatted": (
                        f"{hours}h {minutes:02d}m" if hours else f"{minutes}:{secs:02d}"
                    ),
                }
            )

    return {
        "available": bool(profile) or bool(races),
        "age_years": profile.get("age_years"),
        "gender": profile.get("gender"),
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "bmi": bmi,
        "bmi_band": bmi_key,
        "bmi_label": policy.BMI_BANDS[bmi_key]["label"] if bmi_key else None,
        "bmi_tone": policy.BMI_BANDS[bmi_key]["tone"] if bmi_key else "slate",
        "vo2max": profile.get("vo2max"),
        "lactate_threshold_hr": profile.get("lactate_threshold_hr"),
        "activity_level": profile.get("activity_level"),
        "devices": profile.get("devices") or [],
        "race_predictions": races,
        "race_source_date": (race_predictions or {}).get("date"),
        "intensity": {
            "weekly_total": weekly_minutes,
            "moderate": (intensity or {}).get("moderate"),
            "vigorous": (intensity or {}).get("vigorous"),
            "goal": (intensity or {}).get("goal") or policy.INTENSITY_GOAL_MINUTES,
            "band": intensity_key,
            "label": policy.INTENSITY_BANDS[intensity_key]["label"] if intensity_key else None,
            "tone": policy.INTENSITY_BANDS[intensity_key]["tone"] if intensity_key else "slate",
            "plain": policy.INTENSITY_BANDS[intensity_key]["plain"] if intensity_key else None,
        },
        "day": {
            "steps": (daily_activity or {}).get("steps"),
            "step_goal": (daily_activity or {}).get("step_goal"),
            "floors": (daily_activity or {}).get("floors"),
            "active_kcal": (daily_activity or {}).get("active_kcal"),
            "total_kcal": (daily_activity or {}).get("total_kcal"),
        },
        "date": today_str,
    }


def daily_channel_series(sleep_history, hrv_history, rhr_history, steps_history, spo2_days):
    """The daily channels a correlation is allowed to run over, by date.

    Only channels that were actually measured on a day appear for that day, which
    is what keeps a correlation's paired-day count honest.
    """
    window = policy.CORRELATION_WINDOW_DAYS
    cutoff = None
    for source in (sleep_history, hrv_history, rhr_history, steps_history, spo2_days):
        for record in source or []:
            date = record.get("date") or record.get("calendarDate")
            if date and (cutoff is None or date > cutoff):
                cutoff = date
    if cutoff is None:
        return {}

    def within(date):
        if not date:
            return False
        year, month, day = (int(p) for p in cutoff.split("-"))
        end = datetime.date(year, month, day)
        record = datetime.date(*(int(p) for p in date.split("-")))
        return 0 <= (end - record).days < window

    series = {key: {} for key in policy.CORRELATION_METRICS}

    for night in sleep_history or []:
        date = night.get("date")
        if not within(date):
            continue
        total = night.get("total_seconds") or 0
        if night.get("score") is not None:
            series["sleep_score"][date] = night["score"]
        if night.get("avg_stress") is not None:
            series["sleep_stress"][date] = night["avg_stress"]
        if night.get("avg_respiration") is not None:
            series["respiration"][date] = night["avg_respiration"]
        if total:
            series["sleep_hours"][date] = round(total / 3600.0, 2)
            series["deep_pct"][date] = round((night.get("deep_seconds") or 0) / total * 100, 1)
            series["rem_pct"][date] = round((night.get("rem_seconds") or 0) / total * 100, 1)

    for record in hrv_history or []:
        date, value = record.get("calendarDate"), record.get("lastNightAvg")
        if within(date) and value is not None:
            series["hrv"][date] = value

    for record in rhr_history or []:
        date, value = record.get("calendarDate"), record.get("value")
        if within(date) and value is not None:
            series["rhr"][date] = value

    for record in steps_history or []:
        date = record.get("calendarDate") or record.get("date")
        value = record.get("totalSteps", record.get("steps"))
        if within(date) and value:  # a day with no step count is not a zero
            series["steps"][date] = value

    for day in spo2_days or []:
        date = day.get("date")
        value = day.get("sleep_average") or day.get("latest")
        if within(date) and value is not None:
            series["spo2"][date] = value

    return {key: values for key, values in series.items() if values}


# --- composite scores (ported from the browser) ----------------------------

def hrv_delta(today, baselines):
    """Overnight HRV versus the athlete's own 30-day baseline."""
    hrv = _truthy(today.get("hrv_last_night"), 60)
    base = _truthy(baselines.get("hrv_30d"), 55.3)
    delta_pct = ((hrv - base) / base) * 100.0 if base else 0.0
    return hrv, base, delta_pct


def _acwr(fitness):
    raw = _truthy(fitness.get("acwr"), 0.2)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.2


def calculate_readiness(today, baselines, fitness, whoop, intelligence):
    """Composite training readiness (0-100) with its band and contributions.

    The arithmetic is exactly what the dashboard used to run while rendering:
    the clinical engine's recovery score adjusted by HRV departure, workload
    balance and accumulated sleep debt.
    """
    hrv, hrv_base, delta = hrv_delta(today, baselines)
    acwr = _acwr(fitness)
    sleep_debt = _truthy(whoop.get("accumulated_7d_debt_hours"), 4.7)
    charged = _nullish(today.get("body_battery_charged"), 38)
    score = _truthy(intelligence.get("recovery_score"), 76)

    if delta > 10:
        score += 8
    elif delta > 0:
        score += 4
    elif delta < -15:
        score -= 10
    elif delta < 0:
        score -= 5

    if 0.8 <= acwr <= 1.3:
        score += 5
    elif acwr > 1.5:
        score -= 10

    if sleep_debt < 2:
        score += 5
    elif sleep_debt > 6:
        score -= 8

    score = int(clamp(round(score), 0, 100))
    band = policy.readiness_band(score)

    return {
        "score": score,
        "band": band,
        "badge": policy.READINESS_BANDS[band]["badge"],
        "tone": policy.READINESS_BANDS[band]["tone"],
        "text": policy.READINESS_BANDS[band]["text"],
        "briefing": policy.READINESS_BANDS[band]["briefing"],
        "body_battery_charged": charged,
        "hrv_delta_pct": round(delta, 1),
        "hrv_baseline": hrv_base,
        "hrv_tone": policy.HRV_BANDS[policy.hrv_band(hrv, hrv_base)]["tone"],
        "workload_band": policy.acwr_workload_band(acwr),
        "acwr": acwr,
        "sleep_debt_hours": sleep_debt,
    }


def _injury_factors(acwr, hrv_delta_pct, sleep_debt, recovery_score):
    """The four risk contributors shown on the injury card, computed not asserted."""
    factors = []

    if acwr > 1.5:
        factors.append(("Workload Spike", f"Severe — ACWR {acwr:.1f}", "rose",
                        "Sudden load spike; deload immediately."))
    elif acwr > 1.3:
        factors.append(("Workload Spike", f"Elevated — ACWR {acwr:.1f}", "amber",
                        "Approaching the danger zone; hold volume steady."))
    elif acwr < 0.5:
        factors.append(("Workload Spike", f"Fresh — ACWR {acwr:.1f}", "cyan",
                        "Low recent load; rebuild volume gradually."))
    else:
        factors.append(("Workload Spike", f"None — ACWR {acwr:.1f}", "green",
                        "No sudden training spike detected. Safe to build."))

    if hrv_delta_pct < -15:
        factors.append(("HRV Trend", f"Suppressed ({hrv_delta_pct:+.1f}%)", "rose",
                        "Nervous system under strain; recovery is the priority."))
    elif hrv_delta_pct < 0:
        factors.append(("HRV Trend", f"Below baseline ({hrv_delta_pct:+.1f}%)", "amber",
                        "Slightly suppressed; monitor fatigue over the next days."))
    else:
        factors.append(("HRV Trend", f"Positive ({hrv_delta_pct:+.1f}%)", "green",
                        "Rising HRV = well-adapted, not overreaching."))

    if sleep_debt > 8:
        factors.append(("Sleep Debt", f"High ({sleep_debt:.1f}h)", "rose",
                        "Tissue-repair deficit; prioritise sleep before adding load."))
    elif sleep_debt > 5:
        factors.append(("Sleep Debt", f"Moderate ({sleep_debt:.1f}h)", "amber",
                        "Some fatigue accumulation. Prioritise sleep tonight."))
    else:
        factors.append(("Sleep Debt", f"Low ({sleep_debt:.1f}h)", "green",
                        "Sleep debt is contained; recovery capacity is intact."))

    recovery_band = policy.recovery_band(recovery_score)
    recovery_tone = policy.RECOVERY_TONES[recovery_band]
    zone_word = {"green": "Green", "yellow": "Yellow", "red": "Red"}[recovery_band]
    factors.append((
        "Recovery Score",
        f"{zone_word} Zone ({recovery_score}%)",
        recovery_tone,
        {
            "green": "Body well-recovered. Low systemic strain.",
            "yellow": "Partial recovery. Keep intensity moderate.",
            "red": "Heavily strained. Rest before training hard.",
        }[recovery_band],
    ))

    return [
        {"label": label, "value": value, "tone": tone, "note": note}
        for label, value, tone, note in factors
    ]


def calculate_injury_risk(today, baselines, fitness, whoop, intelligence):
    """Sports-medicine style injury risk (0-100%) with its contributing factors."""
    _, _, delta = hrv_delta(today, baselines)
    acwr = _acwr(fitness)
    sleep_debt = _truthy(whoop.get("accumulated_7d_debt_hours"), 4.7)
    score = _truthy(intelligence.get("recovery_score"), 76)

    risk = 0
    if acwr > 1.5:
        risk += 40
    elif acwr > 1.3:
        risk += 20
    elif acwr < 0.5:
        risk += 5  # detraining
    if delta < -15:
        risk += 25
    elif delta < -8:
        risk += 12
    if sleep_debt > 8:
        risk += 15
    elif sleep_debt > 5:
        risk += 8
    if score < 33:
        risk += 15
    elif score < 50:
        risk += 8

    risk = int(clamp(round(risk), 0, 100))
    band = policy.injury_band(risk)

    return {
        "pct": risk,
        "band": band,
        "badge": policy.INJURY_BANDS[band]["badge"],
        "tone": policy.INJURY_BANDS[band]["tone"],
        "factors": _injury_factors(acwr, delta, sleep_debt, score),
    }
