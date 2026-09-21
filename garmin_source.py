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
import bio_policy as policy

TOKEN_STORE = os.path.expanduser("~/.garminconnect")
DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
SLEEP_CACHE_DIR = CACHE_DIR / "sleep"
SPO2_CACHE_DIR = CACHE_DIR / "spo2"

# This account's activity-weather endpoint answers in Fahrenheit even though the
# profile is metric (a 77 degree dew point is a Singapore evening, not a Baltic
# winter). Values above this are converted; the raw value is kept alongside so the
# conversion is auditable rather than silent.
WEATHER_FAHRENHEIT_ABOVE = 45.0

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


def fetch_fitness_and_workload(client, today_str, profile=None, dq=None):
    """Fitness age and training load, with the athlete's own profile as the base.

    Age and device come from the account rather than a constant, and a value that
    could not be read stays None: the dashboard renders an absent number as "--",
    the same as every other unmeasured value. Inventing a plausible-looking figure
    here is what used to put a stranger's physiology on the athlete's page.
    """
    print("🧬 Fetching Fitness Age and Workload Balance...")
    profile = profile or {}
    devices = profile.get("devices") or []
    primary_device = next((d["name"] for d in devices if d.get("primary")), None)
    if primary_device is None and devices:
        primary_device = devices[0]["name"]

    result = {
        "chronological_age": profile.get("age_years"),
        "fitness_age": None,
        "achievable_fitness_age": None,
        "device_name": primary_device,
        "training_status": None,
        "acute_load": None,
        "chronic_load": None,
        "acwr": None,
        "acwr_status": None,
    }

    fitness_age_live = False
    try:
        fa = client.get_fitnessage_data(today_str)
        if fa:
            if fa.get("chronologicalAge") is not None:
                result["chronological_age"] = fa.get("chronologicalAge")
            if fa.get("fitnessAge") is not None:
                result["fitness_age"] = round(fa.get("fitnessAge"), 1)
            if fa.get("achievableFitnessAge") is not None:
                result["achievable_fitness_age"] = round(fa.get("achievableFitnessAge"), 1)
            fitness_age_live = result["fitness_age"] is not None
            print(f"   • Biological Fitness Age: {result['fitness_age']} yrs (Chronological: {result['chronological_age']} yrs)")
    except Exception as e:
        print(f"   ⚠️ Fitness age unavailable ({e}); the card will report no reading.")

    training_load_live = False
    try:
        ts = client.get_training_status(today_str)
        lts = (ts or {}).get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
        if lts:
            dev_data = lts[list(lts.keys())[0]]
            ac_dto = dev_data.get("acuteTrainingLoadDTO", {})
            result["acute_load"] = ac_dto.get("dailyTrainingLoadAcute")
            result["chronic_load"] = ac_dto.get("dailyTrainingLoadChronic")
            result["acwr"] = ac_dto.get("dailyAcuteChronicWorkloadRatio")
            result["acwr_status"] = ac_dto.get("acwrStatus")
            result["training_status"] = dev_data.get("trainingStatusFeedbackPhrase")
            training_load_live = result["acwr"] is not None
            print(f"   • Acute Load: {result['acute_load']} | Chronic: {result['chronic_load']} | ACWR: {result['acwr']} ({result['acwr_status']})")
    except Exception as e:
        print(f"   ⚠️ Training status unavailable ({e}); the card will report no reading.")

    if dq:
        dq.record(
            "fitness_age",
            fitness_age_live,
            "device fitness age" if fitness_age_live else "no reading (get_fitnessage_data failed)",
        )
        dq.record(
            "training_load",
            training_load_live,
            "device training status" if training_load_live else "no reading (get_training_status failed)",
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
                # Where the session happened, as the device logged it. This is what
                # makes travel visible without asking the athlete anything.
                "locationName": a.get("locationName"),
                "startLatitude": a.get("startLatitude"),
                "startLongitude": a.get("startLongitude"),
                "elevationGain": round(a["elevationGain"], 1) if a.get("elevationGain") is not None else None,
                "steps": a.get("steps"),
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


def _celsius(value):
    """Activity weather in Celsius, keeping Fahrenheit answers honest."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > WEATHER_FAHRENHEIT_ABOVE:
        return round((number - 32.0) * 5.0 / 9.0, 1)
    return round(number, 1)


def fetch_spo2_history(client, dq=None):
    """Pulse Ox readings, cached per day so the API is asked once per date.

    Garmin records this crew on this watch opportunistically: a spot reading on
    most days, an overnight summary on some. The cache is what makes a 120-day
    coverage picture affordable -- a cold cache fills its budget and each run
    tops it up.
    """
    SPO2_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"🫁 Fetching Pulse Ox history ({policy.SPO2_LOOKBACK_DAYS} days, cached)...")

    days = []
    fetched = 0
    for i in range(policy.SPO2_LOOKBACK_DAYS):
        target = (date.today() - timedelta(days=i)).isoformat()
        cache_file = SPO2_CACHE_DIR / f"{target}.json"
        payload = None
        if cache_file.exists():
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        if payload is None:
            if fetched >= policy.SPO2_MAX_NEW_FETCHES_PER_RUN:
                continue
            try:
                payload = _spo2_day(client.get_spo2_data(target), target)
                fetched += 1
                if i > 0:  # today is still being written
                    cache_file.write_text(json.dumps(payload), encoding="utf-8")
                time.sleep(0.12)
            except Exception as e:
                print(f"   ⚠️ Pulse Ox for {target} unavailable ({e}).")
                continue
        if payload and (payload.get("latest") is not None or payload.get("sleep_average") is not None):
            days.append(payload)

    days.sort(key=lambda d: d["date"])
    recorded = len(days)
    cached = sum(1 for i in range(policy.SPO2_LOOKBACK_DAYS)
                 if (SPO2_CACHE_DIR / f"{(date.today() - timedelta(days=i)).isoformat()}.json").exists())
    pending = policy.SPO2_LOOKBACK_DAYS - cached
    print(
        f"   ✅ {recorded} of the last {policy.SPO2_LOOKBACK_DAYS} days carried a reading "
        f"({fetched} fetched now, {cached} already cached"
        + (f", {pending} still to backfill at {policy.SPO2_MAX_NEW_FETCHES_PER_RUN}/run)" if pending else ")")
    )
    if dq:
        dq.record(
            "spo2",
            recorded > 0,
            f"{recorded} of {policy.SPO2_LOOKBACK_DAYS} days had a Pulse Ox reading"
            if recorded
            else f"no Pulse Ox reading in the last {policy.SPO2_LOOKBACK_DAYS} days",
        )
    return days


def _spo2_day(raw, target):
    """One day of Pulse Ox, keeping what the watch actually measured."""
    raw = raw or {}
    return {
        "date": target,
        "latest": raw.get("latestSpO2"),
        "latest_time_local": raw.get("latestSpO2TimestampLocal"),
        "average": raw.get("averageSpO2"),
        "sleep_average": raw.get("avgSleepSpO2"),
        "lowest": raw.get("lowestSpO2"),
        "seven_day_avg": raw.get("lastSevenDaysAvgSpO2"),
    }


def fetch_profile(client, today_str, dq=None):
    """The account's own profile and capacity numbers.

    These replace the values the pipeline used to assume. Age comes from the birth
    date on the account, body composition from its height and weight, and VO2max
    and heat/altitude acclimation from the max-metrics endpoint -- so no personal
    fact reaches the dashboard that the account did not supply.
    """
    print("🙋 Fetching athlete profile and capacity metrics...")
    profile = {
        "gender": None,
        "birth_date": None,
        "age_years": None,
        "height_cm": None,
        "weight_kg": None,
        "vo2max": None,
        "lactate_threshold_hr": None,
        "activity_level": None,
        "devices": [],
        "heat_acclimation_pct": None,
        "altitude_acclimation_pct": None,
    }
    live = False
    try:
        raw = client.get_user_profile() or {}
        user = raw.get("userData", {})
        profile["gender"] = user.get("gender")
        profile["birth_date"] = user.get("birthDate")
        profile["activity_level"] = user.get("activityLevel")
        if user.get("height"):
            profile["height_cm"] = round(float(user["height"]), 1)
        if user.get("weight"):
            profile["weight_kg"] = round(float(user["weight"]) / 1000.0, 1)  # grams
        profile["vo2max"] = user.get("vo2MaxRunning")
        profile["lactate_threshold_hr"] = user.get("lactateThresholdHeartRate")
        birth_date = user.get("birthDate")
        if birth_date:
            born = date.fromisoformat(birth_date)
            today = date.fromisoformat(today_str)
            profile["age_years"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        live = bool(profile["birth_date"] or profile["height_cm"])
        print(
            f"   • Profile: {profile['gender']} {profile['age_years']}y | "
            f"{profile['height_cm']} cm | {profile['weight_kg']} kg | VO2max {profile['vo2max']}"
        )
    except Exception as e:
        print(f"   ⚠️ Profile unavailable ({e}); capacity card will report what it has.")

    try:
        metrics = client.get_max_metrics(today_str) or []
        latest = metrics[-1] if metrics else {}
        acclimation = latest.get("heatAltitudeAcclimation") or {}
        profile["heat_acclimation_pct"] = acclimation.get("heatAcclimationPercentage")
        profile["altitude_acclimation_pct"] = acclimation.get("altitudeAcclimationPercentage")
        generic = latest.get("generic") or {}
        if generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue"):
            profile["vo2max"] = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
            live = True
    except Exception as e:
        print(f"   ⚠️ Max-metrics (VO2max, acclimation) unavailable ({e}).")

    try:
        primary = client.get_primary_training_device() or {}
        wearables = (primary.get("WearableDevices") or {}).get("deviceWeights") or []
        profile["devices"] = [
            {"name": d.get("displayName"), "primary": bool(d.get("primaryWearableDevice"))}
            for d in wearables
            if d.get("displayName")
        ]
    except Exception as e:
        print(f"   ⚠️ Device list unavailable ({e}).")

    if dq:
        dq.record(
            "profile",
            live,
            "account profile, body metrics and VO2max"
            if live
            else "profile endpoints returned nothing",
        )
    return profile


def fetch_activity_context(client, activities, dq=None):
    """Where training happened, and the climate it happened in.

    Locations are the device's own activity records. The weather call is made once,
    for the most recent activity, and is current conditions rather than a series.
    """
    print("🗺️ Building location history and reading the latest session weather...")
    location_days = []
    for act in activities or []:
        start = str(act.get("startTimeLocal") or "")
        date_str = start.split(" ")[0] if start else None
        if date_str:
            location_days.append(
                {
                    "date": date_str,
                    "location": act.get("locationName"),
                    "latitude": act.get("startLatitude"),
                    "longitude": act.get("startLongitude"),
                }
            )
    location_days.sort(key=lambda d: d["date"])

    weather = {}
    weather_live = False
    activities_with_id = [a for a in (activities or []) if a.get("activityId")]
    if activities_with_id:
        newest = max(activities_with_id, key=lambda a: str(a.get("startTimeLocal") or ""))
        try:
            raw = client.get_activity_weather(newest["activityId"]) or {}
            weather = {
                "temp_c": _celsius(raw.get("temp")),
                "feels_like_c": _celsius(raw.get("apparentTemp")),
                "dew_point_c": _celsius(raw.get("dewPoint")),
                "humidity_pct": raw.get("relativeHumidity"),
                "wind_kph": round(float(raw["windSpeed"]) * 1.60934, 1) if raw.get("windSpeed") else None,
                "wind_compass": raw.get("windDirectionCompassPoint"),
                "station": (raw.get("weatherStationDTO") or {}).get("name"),
                "condition": (raw.get("weatherTypeDTO") or {}).get("weatherType"),
                "observed_for_activity": newest.get("activityName"),
                "observed_at": newest.get("startTimeLocal"),
            }
            weather_live = weather.get("temp_c") is not None
            print(
                f"   • {weather['temp_c']}C (feels {weather['feels_like_c']}C), "
                f"{weather['humidity_pct']}% humidity at {weather['station']}"
            )
        except Exception as e:
            print(f"   ⚠️ Session weather unavailable ({e}).")

    if dq:
        named = sum(1 for d in location_days if d["location"])
        dq.record(
            "environment",
            named > 0,
            f"{named} sessions with a location, weather for the latest"
            if named
            else "no located sessions in the activity window",
        )
    return {"location_days": location_days, "weather": weather, "weather_live": weather_live}


def fetch_daily_activity(client, today_str, dq=None):
    """Today's movement totals: steps against goal, floors and calories."""
    try:
        summary = client.get_user_summary(today_str) or {}
    except Exception as e:
        print(f"   ⚠️ Daily activity totals unavailable ({e}).")
        if dq:
            dq.record("daily_activity", False, "user summary unavailable")
        return {}

    result = {
        "steps": summary.get("totalSteps"),
        "step_goal": summary.get("dailyStepGoal"),
        "floors": round(float(summary["floorsAscended"]), 1) if summary.get("floorsAscended") is not None else None,
        "active_kcal": summary.get("activeKilocalories"),
        "total_kcal": summary.get("totalKilocalories"),
        "intensity_goal": summary.get("intensityMinutesGoal"),
    }
    if dq:
        dq.record(
            "daily_activity",
            result.get("steps") is not None,
            "daily summary totals" if result.get("steps") is not None else "daily totals unavailable",
        )
    return result


def fetch_daily_steps_history(client, days, dq=None):
    """Daily steps over the correlation window, in one range request."""
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = client.get_daily_steps(start, date.today().isoformat()) or []
    except Exception as e:
        print(f"   ⚠️ Daily step history unavailable ({e}).")
        if dq:
            dq.record("steps_history", False, "daily steps endpoint failed")
        return []
    if dq:
        dq.record("steps_history", bool(rows), f"{len(rows)} days of step totals")
    return rows


def fetch_hydration(client, today_str, dq=None):
    """Hydration target, logged intake and the day's measured sweat loss."""
    try:
        raw = client.get_hydration_data(today_str) or {}
    except Exception as e:
        print(f"   ⚠️ Hydration unavailable ({e}).")
        if dq:
            dq.record("hydration", False, "hydration endpoint unavailable")
        return {}
    result = {
        "goal_ml": raw.get("goalInML"),
        "intake_ml": raw.get("valueInML"),
        "sweat_loss_ml": raw.get("sweatLossInML"),
    }
    if dq:
        dq.record("hydration", result["goal_ml"] is not None, "hydration goal and sweat loss")
    return result


def fetch_intensity_and_races(client, today_str, intensity_goal, dq=None):
    """Weekly intensity minutes and the device's own race-time forecasts."""
    intensity = {"weekly_total": None, "moderate": None, "vigorous": None, "goal": intensity_goal}
    try:
        raw = client.get_intensity_minutes_data(today_str) or {}
        intensity = {
            "weekly_total": raw.get("weeklyTotal"),
            "moderate": raw.get("weeklyModerate"),
            "vigorous": raw.get("weeklyVigorous"),
            "goal": raw.get("weekGoal") or intensity_goal,
        }
    except Exception as e:
        print(f"   ⚠️ Intensity minutes unavailable ({e}).")

    races = {}
    try:
        raw = client.get_race_predictions() or {}
        races = {
            "date": raw.get("calendarDate"),
            "time5k": raw.get("time5K"),
            "time10k": raw.get("time10K"),
            "time_half": raw.get("timeHalfMarathon"),
            "time_marathon": raw.get("timeMarathon"),
        }
    except Exception as e:
        print(f"   ⚠️ Race predictions unavailable ({e}).")

    if dq:
        dq.record("intensity_minutes", intensity["weekly_total"] is not None, "weekly intensity minutes")
        dq.record("race_predictions", bool(races.get("time5k")), "device race-time forecasts")
    return intensity, races


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
