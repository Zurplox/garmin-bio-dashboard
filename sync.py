"""
Garmin Historical Biometrics Sync -- orchestration layer.

Pipeline:  garmin_source  ->  bio_analytics  ->  clinical_engine  ->  payload
                  |                                  |
             provenance.py                      bio_policy.py
                                                    |
                                       (bands, tones, labels,
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
    `bio_analytics` picks the windows and the latest record itself."""
    today_str = date.today().isoformat()
    return {
        "today_str": today_str,
        "rhr": source.fetch_multi_year_rhr(client, dq),
        "hrv": source.fetch_multi_year_hrv(client, dq),
        "sleep": source.fetch_sleep_history(client, days=210, dq=dq),
        "fitness": source.fetch_fitness_and_workload(client, today_str, dq),
        "activities": source.fetch_recent_activities(client, limit=60, dq=dq),
    }


def build_payload(client, fetched, dq):
    """Analyse the fetched data and assemble the encrypted payload."""
    today_str = fetched["today_str"]
    all_rhr, all_hrv, sleep_history = fetched["rhr"], fetched["hrv"], fetched["sleep"]
    fitness_data, activities = fetched["fitness"], fetched["activities"]

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

    intelligence = clinical_engine.synthesize(today_snapshot, baselines, fitness_data)

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
    print(f"   • Data provenance: {quality['live_count']}/{quality['total_count']} metric groups from Garmin ({quality['live_pct']}%)")
    for metric in quality["metrics"].values():
        if metric["source"] != "live":
            print(f"       ⚠️ {metric['label']}: {metric['note']}")
    print("=" * 65)


if __name__ == "__main__":
    main()
