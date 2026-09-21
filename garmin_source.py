"""
Garmin data access: authentication, endpoint calls, and provenance recording.

This is the only module that talks to Garmin. Each fetcher records whether its
value came from the API or from a documented fallback, so nothing downstream has
to guess whether a number was measured. Parsing stays shallow here -- shaping a
response into a payload field is analytics' job, deciding what a number means is
policy's job.
"""

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import bio_analytics as analytics

TOKEN_STORE = os.path.expanduser("~/.garminconnect")
DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
SLEEP_CACHE_DIR = CACHE_DIR / "sleep"

# Daily RHR/HRV history is pulled in yearly batches from 2022 onward.
HISTORY_RANGES = [
    ("2022-08-01", "2022-12-31"),
    ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"),
    ("2025-01-01", "2025-12-31"),
    ("2026-01-01", None),  # None == today
]


def get_authenticated_client():
    from garminconnect import Garmin

    token_file = Path(TOKEN_STORE) / "garmin_tokens.json"
    if not token_file.exists():
        print("❌ Error: garmin_tokens.json not found. Run verify_garmin.py first.")
        sys.exit(1)

    client = Garmin()
    client.login(tokenstore=TOKEN_STORE)
    return client


def _resolved_ranges():
    today = date.today().isoformat()
    return [(start, end or today) for start, end in HISTORY_RANGES]


def fetch_multi_year_rhr(client, dq=None):
    """Resting heart rate, daily, from 2022 to present in yearly batches."""
    print("📈 Fetching multi-year Resting Heart Rate (2022-2026)...")
    all_rhr = []
    for start, end in _resolved_ranges():
        try:
            data = client.get_rhr_daily(start, end)
            if data:
                valid = [d for d in data if d.get("value") is not None]
                all_rhr.extend(valid)
                print(f"   • {start[:4]}: {len(valid)} valid days")
            time.sleep(0.2)
        except Exception as e:
            print(f"   ⚠️ Error fetching RHR for {start} to {end}: {e}")

    if dq:
        dq.record("rhr", bool(all_rhr), f"{len(all_rhr)} daily readings")

    return all_rhr


def fetch_multi_year_hrv(client, dq=None):
    """Overnight HRV summaries, daily, from 2022 to present."""
    print("❤️ Fetching multi-year Heart Rate Variability (HRV)...")
    all_hrv = []
    for start, end in _resolved_ranges():
        try:
            data = client.get_hrv_data_range(start, end)
            if data and "hrvSummaries" in data:
                sums = data["hrvSummaries"]
                all_hrv.extend(sums)
                print(f"   • {start[:4]}: {len(sums)} HRV summaries")
            time.sleep(0.2)
        except Exception as e:
            print(f"   ⚠️ Error fetching HRV for {start} to {end}: {e}")

    valid_hrv = [h for h in all_hrv if h.get("lastNightAvg")]
    if dq:
        dq.record("hrv", bool(valid_hrv), f"{len(valid_hrv)} nightly summaries")

    return valid_hrv


def fetch_sleep_history(client, days=210, dq=None):
    """High-resolution daily sleep with local JSON caching, up to 210 days (>6 months)."""
    SLEEP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"🛌 Fetching past {days} days of sleep architecture (cached)...")

    sleep_history = []
    fetched_remote = 0

    for i in range(days):
        target_date = (date.today() - timedelta(days=i)).isoformat()
        cache_file = SLEEP_CACHE_DIR / f"{target_date}.json"

        data = None
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None

        if data is None:
            try:
                data = client.get_sleep_data(target_date)
                if i > 0 and data:  # never cache today; it is still being written
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                fetched_remote += 1
                time.sleep(0.15)
            except Exception as e:
                print(f"   ⚠️ Error fetching sleep for {target_date}: {e}")

        if data:
            dto = data.get("dailySleepDTO", {})
            total_sec = dto.get("sleepTimeSeconds", 0)
            if total_sec and total_sec > 3600:
                sleep_history.append({
                    "date": target_date,
                    "score": dto.get("sleepScores", {}).get("overall", {}).get("value"),
                    "total_seconds": total_sec,
                    "deep_seconds": dto.get("deepSleepSeconds", 0),
                    "rem_seconds": dto.get("remSleepSeconds", 0),
                    "light_seconds": dto.get("lightSleepSeconds", 0),
                    "awake_seconds": dto.get("awakeSleepSeconds", 0),
                    "avg_stress": dto.get("avgSleepStress"),
                    "avg_respiration": dto.get("averageRespirationValue"),
                    "lowest_respiration": dto.get("lowestRespirationValue"),
                    "bedtime_minutes": analytics.bedtime_minutes_from_record(dto),
                })

    print(f"   ✅ Processed {len(sleep_history)} sleep records ({fetched_remote} newly fetched, {len(sleep_history) - fetched_remote} from cache)")

    if dq:
        dq.record("sleep", bool(sleep_history), f"{len(sleep_history)} nights of sleep architecture")

    return sleep_history


def fetch_fitness_and_workload(client, today_str, dq=None):
    """Fitness age, training load, ACWR and device telemetry.

    Fields that could not be read from Garmin keep their documented placeholder
    and the provenance tracker reports them as fallbacks.
    """
    print("🧬 Fetching Fitness Age and Workload Balance...")
    result = {
        "chronological_age": 29,
        "fitness_age": 24.7,
        "achievable_fitness_age": 21.0,
        "device_name": "fenix 6S ASIA Sapphire",
        "training_status": "RECOVERY",
        "acute_load": 44,
        "chronic_load": 219,
        "acwr": 0.2,
        "acwr_status": "LOW",
        "heat_acclimation_pct": 5,
        "bmi": 22.8,
    }

    fitness_age_live = False
    try:
        fa = client.get_fitnessage_data(today_str)
        if fa:
            result["chronological_age"] = fa.get("chronologicalAge", 29)
            result["fitness_age"] = round(fa.get("fitnessAge", 24.7), 1)
            result["achievable_fitness_age"] = round(fa.get("achievableFitnessAge", 21.0), 1)
            bmi = fa.get("components", {}).get("bmi", {}).get("value")
            if bmi is not None:
                result["bmi"] = bmi
            fitness_age_live = True
            print(f"   • Biological Fitness Age: {result['fitness_age']} yrs (Chronological: {result['chronological_age']} yrs)")
    except Exception as e:
        print(f"   ⚠️ Fitness age unavailable ({e}); keeping placeholder values.")

    training_load_live = False
    try:
        ts = client.get_training_status(today_str)
        lts = (ts or {}).get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
        if lts:
            dev_data = lts[list(lts.keys())[0]]
            ac_dto = dev_data.get("acuteTrainingLoadDTO", {})
            result["acute_load"] = ac_dto.get("dailyTrainingLoadAcute", 44)
            result["chronic_load"] = ac_dto.get("dailyTrainingLoadChronic", 219)
            result["acwr"] = ac_dto.get("dailyAcuteChronicWorkloadRatio", 0.2)
            result["acwr_status"] = ac_dto.get("acwrStatus", "LOW")
            result["training_status"] = dev_data.get("trainingStatusFeedbackPhrase", "RECOVERY_1")
            training_load_live = True
            print(f"   • Acute Load: {result['acute_load']} | Chronic: {result['chronic_load']} | ACWR: {result['acwr']} ({result['acwr_status']})")
    except Exception as e:
        print(f"   ⚠️ Training status unavailable ({e}); keeping placeholder values.")

    if dq:
        dq.record(
            "fitness_age",
            fitness_age_live,
            "Garmin fitness age" if fitness_age_live else "placeholder constants (get_fitnessage_data failed)",
        )
        dq.record(
            "training_load",
            training_load_live,
            "Garmin training status" if training_load_live else "placeholder constants (get_training_status failed)",
        )

    return result


def fetch_recent_activities(client, limit=60, dq=None):
    """Recent workouts with their activity category normalised for the UI."""
    print(f"🏃 Fetching recent {limit} workout activities...")
    try:
        acts = client.get_activities(0, limit)
        clean_acts = []
        for a in acts:
            type_key = a.get("activityType", {}).get("typeKey", "other")
            category = "Other"
            if "running" in type_key or "treadmill" in type_key:
                category = "Running"
            elif "walking" in type_key or "hiking" in type_key:
                category = "Walking"
            elif "cycling" in type_key or "biking" in type_key:
                category = "Cycling"
            elif "strength" in type_key or "training" in type_key or "cardio" in type_key:
                category = "Gym"

            clean_acts.append({
                "activityId": a.get("activityId"),
                "activityName": a.get("activityName"),
                "activityType": type_key,
                "category": category,
                "startTimeLocal": a.get("startTimeLocal"),
                "distance_km": round((a.get("distance", 0) or 0) / 1000.0, 2),
                "duration_min": round((a.get("duration", 0) or 0) / 60.0, 1),
                "calories": a.get("calories"),
                "averageHR": a.get("averageHR"),
                "maxHR": a.get("maxHR"),
                "aerobicTrainingEffect": a.get("aerobicTrainingEffect"),
                "anaerobicTrainingEffect": a.get("anaerobicTrainingEffect"),
            })
        print(f"   ✅ Fetched {len(clean_acts)} activities")
        if dq:
            dq.record("activities", True, f"{len(clean_acts)} activities")
        return clean_acts
    except Exception as e:
        print(f"   ⚠️ Error fetching activities: {e}")
        if dq:
            dq.record("activities", False, "activity feed unavailable")
        return []


def fetch_stress_distribution(client, date_str, dq=None):
    """Real 24-hour stress architecture from Garmin stress telemetry.

    Garmin samples stress roughly every 3 minutes; values below zero are
    sentinels (-1 = no reading, -2 = activity / off-wrist) and are excluded.
    Returns (distribution, average) or (None, None) when there is too little
    telemetry to be meaningful -- the caller then labels its estimate as such
    instead of presenting it as 24-hour telemetry.
    """
    try:
        data = client.get_stress_data(date_str) or {}
        distribution, average, samples = analytics.stress_distribution_from_samples(
            data.get("stressValuesArray")
        )
        if distribution is None:
            raise ValueError(f"only {samples} usable stress samples")

        print(
            f"   • 24h stress: {average} avg over {samples} samples "
            f"(rest {distribution['rest_pct']}% / low {distribution['low_pct']}% / "
            f"med {distribution['med_pct']}% / high {distribution['high_pct']}%)"
        )
        if dq:
            dq.record("stress_distribution", True, f"{samples} Garmin stress samples")
        return distribution, average
    except Exception as e:
        print(f"   ⚠️ Stress telemetry unavailable ({e}); distribution will be estimated from the daily average.")
        if dq:
            dq.record("stress_distribution", False, "estimated from the daily stress average")
        return None, None


def fetch_today_snapshot(client, today_str, latest_sleep, latest_hrv, all_rhr, dq=None):
    """Fetch today's Body Battery, step/stress summary and stress telemetry,
    then hand them to analytics to assemble the snapshot."""
    body_battery = {}
    bb_live = False
    try:
        bb = client.get_body_battery(today_str)
        if bb:
            today_bb = bb[-1] if isinstance(bb, list) else bb
            charged = today_bb.get("charged")
            if charged is None:
                raise ValueError("Body Battery response contained no 'charged' value")
            body_battery = {"charged": charged, "drained": today_bb.get("drained") or 0}
            bb_live = True
    except Exception as e:
        print(f"   ⚠️ Body Battery unavailable ({e}); keeping placeholder charge value.")

    summary = {}
    summary_live = False
    try:
        summ = client.get_user_summary(today_str)
        if summ:
            stress = summ.get("averageStressLevel")
            steps = summ.get("totalSteps")
            if stress is not None:
                summary["stress_avg"] = stress
            if steps is not None:
                summary["steps"] = steps
            summary_live = stress is not None or steps is not None
        if not summary_live:
            raise ValueError("user summary contained no stress or step values")
        print(f"   • Steps: {summary.get('steps')} | All-day stress average: {summary.get('stress_avg')}")
    except Exception as e:
        print(f"   ⚠️ User summary unavailable ({e}); keeping placeholder step/stress values.")

    distribution, measured_avg = fetch_stress_distribution(client, today_str, dq)
    stress = {"distribution": distribution, "average": measured_avg}

    if dq:
        dq.record("body_battery", bb_live, "Garmin Body Battery" if bb_live else "placeholder charge value")
        dq.record(
            "user_summary",
            summary_live,
            "Garmin daily summary" if summary_live else "placeholder step/stress values",
        )

    return analytics.build_today_snapshot(
        today_str, latest_sleep, latest_hrv, all_rhr, body_battery, summary, stress
    )
