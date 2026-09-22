"""
Garmin Historical Biometrics Sync -- orchestration layer.

Pipeline:  garmin_source  ->  bio_analytics  ->  clinical_engine  ->  payload
                  |               |                  |
                  |          bio_correlate           |
             provenance.py            bio_policy.py (bands, tones, labels,
                                                     shared with the browser)

This file owns the run itself and nothing else: it fetches, calls the analytics,
asks the clinical engine for verdicts, decides whether the dataset is fit to
publish, writes the payload, and reports. Any rule about *what a number means*
belongs in `bio_policy`; any arithmetic belongs in `bio_analytics`. Both halves
of the system read those, so the dashboard renders resolved bands instead of
comparing values against its own copy of the thresholds.
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import bio_analytics as analytics
import bio_coach
import bio_correlate
import bio_policy as policy
import clinical_engine
import garmin_source as source
from provenance import DataQuality

DATA_DIR = Path("data")

# Fix Windows console UTF-8 encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def utc_now_iso():
    """Timezone-aware UTC timestamp.

    A naive timestamp ("2026-09-20T22:33:29") is parsed by browsers as *local*
    time, so a UTC sync time was displayed eight hours off in Singapore.
    """
    return datetime.now(timezone.utc).isoformat()


def collect(client, dq):
    """Fetch every input the payload needs. Ordering is not established here:
    `bio_analytics` picks the windows and the latest record itself.

    The profile is fetched first because age, body metrics and the device name are
    the athlete's own facts, and everything downstream reads them from there.
    """
    today_str = date.today().isoformat()
    profile = source.fetch_profile(client, today_str, dq)
    activities = source.fetch_recent_activities(client, limit=90, dq=dq)
    intensity, races = source.fetch_intensity_and_races(client, today_str, None, dq)
    return {
        "today_str": today_str,
        "profile": profile,
        "rhr": source.fetch_multi_year_rhr(client, dq),
        "hrv": source.fetch_multi_year_hrv(client, dq),
        "sleep": source.fetch_sleep_history(client, days=210, dq=dq),
        "fitness": source.fetch_fitness_and_workload(client, today_str, profile, dq),
        "activities": activities,
        "location": source.fetch_activity_context(client, activities, dq),
        "spo2": source.fetch_spo2_history(client, dq),
        "steps": source.fetch_daily_steps_history(client, policy.CORRELATION_WINDOW_DAYS, dq),
        "hydration": source.fetch_hydration(client, today_str, dq),
        "daily_activity": source.fetch_daily_activity(client, today_str, dq),
        "intensity": intensity,
        "races": races,
    }


def age_advantage_fields(fitness_data):
    """The resolved fitness-age advantage for the badge, absent when unmeasured."""
    advantage = policy.fitness_age_advantage(
        fitness_data.get("fitness_age"), fitness_data.get("chronological_age")
    )
    return {
        "age_advantage": advantage["key"],
        "age_advantage_years": advantage["years"],
        "age_advantage_label": advantage["label"],
        "age_advantage_tone": advantage["tone"],
    }


def acwr_band_fields(fitness_data):
    """The resolved ACWR band for the dashboard, or nothing when unmeasured."""
    acwr = fitness_data.get("acwr")
    if acwr is None:
        return {}
    band = policy.acwr_band(acwr)
    return {
        "acwr_band": band,
        "acwr_band_label": policy.ACWR_BANDS[band]["label"],
        "acwr_band_tone": policy.ACWR_BANDS[band]["tone"],
    }


def build_payload(client, fetched, dq):
    """Analyse the fetched data and assemble the encrypted payload."""
    today_str = fetched["today_str"]
    all_rhr, all_hrv, sleep_history = fetched["rhr"], fetched["hrv"], fetched["sleep"]
    fitness_data, activities = fetched["fitness"], fetched["activities"]
    profile = fetched["profile"]

    baselines, rhr_multi_year = analytics.calculate_baselines(all_rhr, all_hrv, sleep_history)
    circadian = analytics.compute_circadian_architecture(sleep_history)
    dq.record(
        "circadian",
        bool(circadian["available"]),
        (
            f"{circadian['nights_used']} nights, onset spread {circadian['onset_std_dev_minutes']} min"
            if circadian["available"]
            else "insufficient sleep-onset telemetry"
        ),
    )

    today_snapshot = source.fetch_today_snapshot(
        client,
        today_str,
        analytics.latest_record(sleep_history, "date"),
        analytics.latest_record(all_hrv, "calendarDate"),
        all_rhr,
        dq,
    )

    # The signals the pipeline used to leave on the table: blood oxygen, where the
    # training happened and what the air was doing, capacity from the athlete's own
    # profile, and the correlations between channels. They are built before the
    # clinical engine runs so the narrative is written from the same measurements.
    oxygen = analytics.build_oxygen_signal(fetched["spo2"])
    environment = analytics.build_environment_signal(
        fetched["location"]["location_days"],
        fetched["location"]["weather"],
        profile.get("heat_acclimation_pct"),
        fetched["hydration"],
    )
    capacity = analytics.build_capacity_signal(
        profile, fetched["races"], fetched["intensity"], fetched["daily_activity"], today_str
    )
    channels = analytics.daily_channel_series(
        sleep_history, all_hrv, all_rhr, fetched["steps"], fetched["spo2"]
    )
    correlations = {
        **bio_correlate.build_correlations(channels),
        "location": bio_correlate.location_breakdown(
            fetched["location"]["location_days"], channels
        ),
    }

    intelligence = clinical_engine.synthesize(
        today_snapshot,
        baselines,
        fitness_data,
        # The measured facts the prose may use, and the only ones.
        {
            "profile": profile,
            "oxygen": oxygen,
            "environment": environment,
            "capacity": capacity,
            "correlations": correlations,
        },
    )

    whoop_data = analytics.calculate_whoop_metrics(
        today_snapshot, activities, sleep_history, intelligence
    )
    fitbit_data = analytics.calculate_fitbit_metrics(today_snapshot, baselines, fitness_data)
    garmin_sig = analytics.build_garmin_signature(today_snapshot, circadian)

    # Composite scores are computed by the engine rather than re-derived in the
    # browser, so the dashboard and the payload can never disagree about them.
    readiness = analytics.calculate_readiness(
        today_snapshot, baselines, fitness_data, whoop_data, intelligence
    )
    injury_risk = analytics.calculate_injury_risk(
        today_snapshot, baselines, fitness_data, whoop_data, intelligence
    )

    # The ratio's band is resolved once here and read by both the dashboard and the
    # coach, so no prescription re-derives a band of its own.
    fitness_data = {**fitness_data, **acwr_band_fields(fitness_data), **age_advantage_fields(fitness_data)}
    # HRV's per-night bands, resolved here for the same reason: the chart's scrub
    # HUD reads the night's own band instead of Garmin's status word.
    night_bands = analytics.hrv_night_bands(all_hrv)
    coaching = bio_coach.build_coaching(
        today_str,
        activities,
        fetched["steps"],
        sleep_history,
        whoop_data,
        baselines,
        fitness_data,
        readiness,
        environment,
        today_snapshot,
        all_hrv,
    )

    quality = dq.as_dict()
    if not dq.publishable():
        print("\n" + "!" * 65)
        print("⛔ ABORTING RUN: core biometric sources failed:")
        for label in quality["core_degraded"]:
            print(f"   • {label}")
        print("Refusing to publish a dataset assembled from placeholders, because the")
        print("dashboard cannot distinguish them from measurements. The previously")
        print("deployed vault is left untouched.")
        print("!" * 65)
        sys.exit(1)

    return {
        "updated_at": utc_now_iso(),
        "policy": policy.policy_snapshot(),
        "athlete": {
            "name": client.full_name or "Harvin",
            "display_name": client.display_name,
            "chronological_age": fitness_data["chronological_age"],
            "fitness_age": fitness_data["fitness_age"],
            "primary_device": fitness_data["device_name"],
        },
        "today": today_snapshot,
        # A ratio has one set of band names, and policy owns them, so the badge, the
        # readiness card and the coach cannot label the same 0.1 differently.
        "fitness": fitness_data,
        "whoop": whoop_data,
        "fitbit": fitbit_data,
        "garmin_signature": garmin_sig,
        "readiness": readiness,
        "injury_risk": injury_risk,
        "baselines": baselines,
        "history": {
            "daily_sleep": sleep_history,
            "daily_hrv": [
                {
                    "date": h.get("calendarDate"),
                    "lastNightAvg": h.get("lastNightAvg"),
                    "weeklyAvg": h.get("weeklyAvg"),
                    "status": h.get("status"),
                    # The night's own band, resolved by policy on the same 30-night
                    # basis the KPI card reads, so no HRV surface can give one
                    # reading a second verdict.
                    **night_bands.get(h.get("calendarDate"), {}),
                }
                for h in all_hrv[-210:]
            ],
            "daily_rhr": [
                {"date": r.get("calendarDate"), "rhr": r.get("value")} for r in all_rhr[-210:]
            ],
            "rhr_multi_year": rhr_multi_year,
            "activities": activities,
        },
        "clinical_intelligence": intelligence,
        # Measured signal groups. Each one carries whether it was measured and, where
        # the data is sparse by nature, how sparse.
        "oxygen": oxygen,
        "environment": environment,
        "capacity": capacity,
        "correlations": correlations,
        "coaching": coaching,
        "data_quality": quality,
    }


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dq = DataQuality()
    client = source.get_authenticated_client()

    print("=" * 65)
    print(f"🧬 COMPREHENSIVE BIOMETRIC EXTRACTION // ATHLETE: {client.full_name or 'HARVIN'}")
    print("=" * 65)

    fetched = collect(client, dq)
    payload = build_payload(client, fetched, dq)

    out_file = DATA_DIR / "biometrics.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    quality = payload["data_quality"]
    intelligence = payload["clinical_intelligence"]
    print("\n" + "=" * 65)
    print(f"🎉 SUCCESS! Clinical biometrics saved to: {out_file.resolve()}")
    print(f"   • Total RHR days: {len(fetched['rhr'])}")
    print(f"   • Total HRV summaries: {len(fetched['hrv'])}")
    print(f"   • >6-Month Sleep records: {len(fetched['sleep'])}")
    print(f"   • Fitness Age: {payload['fitness']['fitness_age']} yrs | ACWR: {payload['fitness']['acwr']}")
    print(f"   • Recovery Score: {intelligence['recovery_score']}% ({intelligence['recovery_zone']})")
    print(f"   • Readiness: {payload['readiness']['score']}/100 ({payload['readiness']['badge']}) | Injury risk: {payload['injury_risk']['pct']}% ({payload['injury_risk']['badge']})")
    print(f"   • Illness Early Warning: {intelligence['illness_early_warning']['risk_level']} ({intelligence['illness_early_warning']['status_headline']})")
    print(f"   • Data provenance: {quality['live_count']}/{quality['total_count']} metric groups live ({quality['live_pct']}%)")
    oxygen, environment = payload["oxygen"], payload["environment"]
    print(
        f"   • Blood oxygen: {oxygen['days_recorded']}/{oxygen['window_days']} days recorded"
        + (f" | sleep-time avg {oxygen['sleep_average']}%" if oxygen["sleep_average"] else " | no sleep-time average")
    )
    print(
        f"   • Location: {environment['home_location']} ({len(environment['locations'])} logged) | "
        f"climate {environment['weather'].get('temp_c')}C {environment['weather'].get('humidity_pct')}%"
    )
    print(f"   • Correlations published: {len(payload['correlations']['findings'])} of {payload['correlations']['tested_pairs']} curated pairs")
    for metric in quality["metrics"].values():
        if metric["source"] != "live":
            print(f"       ⚠️ {metric['label']}: {metric['note']}")
    print("=" * 65)


if __name__ == "__main__":
    main()
