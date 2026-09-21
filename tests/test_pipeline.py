"""
Regression suite for the biometric pipeline.

Run with:  python -m unittest discover -s tests -t .

Everything here is offline: no Garmin session, no network, no secrets. Tests are
grouped by the module that owns the behaviour they guard:

    bio_policy      -- thresholds, bands and tones (imported directly)
    bio_analytics   -- clock/time, baselines, windows, strain, scores
    provenance      -- live vs fallback recording
    garmin_source   -- endpoint parsing and today's snapshot
    clinical_engine -- AI validation and the deterministic rule engine
    sync            -- payload assembly and the publish gate
"""

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone

import bio_analytics as analytics
import bio_policy as policy
import clinical_engine
import encrypt_data
import garmin_source as source
import sync
from provenance import DataQuality


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sleep(date_str, hours=7.5, deep_pct=22.0, rem_pct=17.0, bedtime=None, score=80):
    total = int(hours * 3600)
    return {
        "date": date_str,
        "score": score,
        "total_seconds": total,
        "deep_seconds": int(total * deep_pct / 100),
        "rem_seconds": int(total * rem_pct / 100),
        "light_seconds": int(total * (100 - deep_pct - rem_pct) / 100),
        "awake_seconds": 0,
        "avg_stress": 16.0,
        "avg_respiration": 13.0,
        "lowest_respiration": 9.0,
        "bedtime_minutes": bedtime,
    }


def make_rhr(start_date, days, value=50.0):
    start = datetime.fromisoformat(start_date)
    return [
        {"calendarDate": (start + timedelta(days=i)).date().isoformat(), "value": value + i % 3}
        for i in range(days)
    ]


def make_hrv(start_date, days, value=56):
    start = datetime.fromisoformat(start_date)
    return [
        {"calendarDate": (start + timedelta(days=i)).date().isoformat(),
         "lastNightAvg": value + (i % 5), "weeklyAvg": value, "status": "BALANCED"}
        for i in range(days)
    ]


def make_today(**overrides):
    today = {
        "date": "2026-09-20", "sleep_time_seconds": 27000, "deep_sleep_seconds": 6000,
        "rem_sleep_seconds": 4500, "sleep_score": 80, "sleep_stress": 16.0,
        "respiration_rate": 13.0, "hrv_last_night": 60, "rhr": 50.0, "steps": 3000,
        "stress_avg": 15, "body_battery_charged": 38, "body_battery_drained": 0,
        "stress_distribution": None,
    }
    today.update(overrides)
    return today


def make_fitness(**overrides):
    fitness = {
        "fitness_age": 24.7, "chronological_age": 29, "achievable_fitness_age": 21.0,
        "acute_load": 40, "chronic_load": 200, "acwr": 0.2, "acwr_status": "LOW",
        "device_name": "fenix 6S ASIA Sapphire",
    }
    fitness.update(overrides)
    return fitness


def make_baselines(**overrides):
    baselines = {
        "rhr_30d": 50.0, "hrv_30d": 56.0, "hrv_normal_range": [54, 73],
        "respiration_avg_30d": 13.0, "deep_sleep_pct_180d": 22.0,
    }
    baselines.update(overrides)
    return baselines


class FakeClient:
    """Stands in for garminconnect.Garmin; each call can be made to fail."""

    full_name = "Harvin"
    display_name = "harvin"

    def __init__(self, stress=None, body_battery=None, summary=None):
        self._stress = stress
        self._body_battery = body_battery
        self._summary = summary

    def get_stress_data(self, cdate):
        if isinstance(self._stress, Exception):
            raise self._stress
        return self._stress or {}

    def get_body_battery(self, cdate):
        if isinstance(self._body_battery, Exception):
            raise self._body_battery
        return self._body_battery

    def get_user_summary(self, cdate):
        if isinstance(self._summary, Exception):
            raise self._summary
        return self._summary


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

class ClockFormattingTests(unittest.TestCase):
    def test_twelve_hour_clock_never_mixes_24h_hour_with_am_pm(self):
        # The deployed vault contained "22:03 PM SGT" and "22:15 PM - 22:45 PM SGT".
        cases = {
            1303: "9:43 PM",
            1323: "10:03 PM",
            1350: "10:30 PM",
            30: "12:30 AM",
            0: "12:00 AM",
            720: "12:00 PM",
            1439: "11:59 PM",
        }
        for minutes, expected in cases.items():
            with self.subTest(minutes=minutes):
                self.assertEqual(analytics.format_clock_12h(minutes), expected)

    def test_out_of_range_minutes_wrap(self):
        self.assertEqual(analytics.format_clock_12h(1470), "12:30 AM")
        self.assertEqual(analytics.format_clock_12h(24 * 60 + 90), "1:30 AM")
        self.assertEqual(analytics.format_clock_12h(-30), "11:30 PM")

    def test_utc_now_iso_is_timezone_aware(self):
        parsed = datetime.fromisoformat(sync.utc_now_iso())
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_bedtime_onset_is_evening_anchored(self):
        # 2026-09-19 15:14 UTC == 23:14 SGT -> 1394 minutes
        self.assertEqual(
            analytics.bedtime_minutes_from_record({"sleepStartTimestampGMT": 1758294840000}),
            1394,
        )
        # Missing or zero timestamps must not fabricate an onset.
        self.assertIsNone(analytics.bedtime_minutes_from_record({}))
        self.assertIsNone(analytics.bedtime_minutes_from_record({"sleepStartTimestampGMT": 0}))


class PercentSplitTests(unittest.TestCase):
    def test_always_totals_one_hundred(self):
        cases = [
            {"rest": 210, "low": 60, "med": 18, "high": 6},
            {"rest": 1, "low": 1, "med": 1, "high": 0},
            {"rest": 0, "low": 0, "med": 0, "high": 7},
            {"rest": 333, "low": 333, "med": 333, "high": 1},
        ]
        for counts in cases:
            with self.subTest(counts=counts):
                self.assertEqual(sum(analytics.percent_split(counts).values()), 100)

    def test_empty_input_is_all_zero(self):
        self.assertEqual(sum(analytics.percent_split({"rest": 0, "low": 0}).values()), 0)


# ---------------------------------------------------------------------------
# Stress telemetry
# ---------------------------------------------------------------------------

class StressDistributionTests(unittest.TestCase):
    def test_sentinel_values_are_excluded(self):
        samples = [[i, 20] for i in range(60)] + [[900, -1], [901, -2], [902, None]]
        distribution, average = source.fetch_stress_distribution(FakeClient(stress={"stressValuesArray": samples}), "2026-09-20")
        self.assertEqual(distribution["samples"], 60)
        self.assertEqual(distribution["rest_pct"], 100)
        self.assertEqual(average, 20.0)

    def test_buckets_follow_the_documented_bands(self):
        samples = [[0, 10]] * 25 + [[1, 40]] * 25 + [[2, 60]] * 25 + [[3, 90]] * 25
        distribution, average = source.fetch_stress_distribution(FakeClient(stress={"stressValuesArray": samples}), "2026-09-20")
        self.assertEqual(distribution["rest_pct"], 25)
        self.assertEqual(distribution["low_pct"], 25)
        self.assertEqual(distribution["med_pct"], 25)
        self.assertEqual(distribution["high_pct"], 25)
        self.assertEqual(distribution["samples"], 100)
        self.assertEqual(distribution["band"], policy.stress_band(average))

    def test_insufficient_telemetry_falls_back_and_is_flagged(self):
        dq = DataQuality()
        distribution, average = source.fetch_stress_distribution(FakeClient(stress={"stressValuesArray": [[0, 10]]}), "2026-09-20", dq)
        self.assertIsNone(distribution)
        self.assertIsNone(average)
        self.assertEqual(dq.as_dict()["metrics"]["stress_distribution"]["source"], "fallback")

    def test_endpoint_failure_does_not_raise(self):
        dq = DataQuality()
        distribution, average = source.fetch_stress_distribution(FakeClient(stress=RuntimeError("boom")), "2026-09-20", dq)
        self.assertIsNone(distribution)
        self.assertEqual(dq.as_dict()["degraded"], ["24-hour stress distribution"])


class DataQualityTests(unittest.TestCase):
    def test_live_and_fallback_metrics_are_reported(self):
        dq = DataQuality()
        dq.record("sleep", True, "186 nights")
        dq.record("rhr", True)
        dq.record("hrv", True)
        dq.record("training_load", False, "placeholder constants")
        quality = dq.as_dict()
        self.assertEqual(quality["live_count"], 3)
        self.assertEqual(quality["total_count"], 4)
        self.assertEqual(quality["live_pct"], 75)
        self.assertEqual(quality["degraded"], ["Workload balance (ACWR)"])
        self.assertEqual(quality["core_degraded"], [])

    def test_failed_core_metric_is_flagged_for_the_abort_gate(self):
        dq = DataQuality()
        dq.record("sleep", True)
        dq.record("rhr", False, "no readings")
        dq.record("hrv", True)
        self.assertFalse(dq.publishable())
        self.assertEqual(dq.as_dict()["core_degraded"], ["Resting heart rate"])

    def test_non_core_failure_does_not_abort(self):
        dq = DataQuality()
        for key in policy.CORE_METRICS:
            dq.record(key, True)
        dq.record("body_battery", False)
        self.assertTrue(dq.publishable())
        self.assertEqual(dq.as_dict()["core_degraded"], [])


# ---------------------------------------------------------------------------
# Baselines and series windows
# ---------------------------------------------------------------------------

class BaselineTests(unittest.TestCase):
    def test_recent_windows_use_the_latest_dates_not_input_order(self):
        rhr = make_rhr("2026-06-01", 200, value=45.0)
        shuffled = rhr[::-1]  # newest first, as some endpoints return it
        sleep = [make_sleep(f"2026-0{month}-{day:02d}") for month in (1, 2) for day in range(1, 11)]
        baselines, multi_year = analytics.calculate_baselines(shuffled, make_hrv("2026-06-01", 200), sleep)

        newest_thirty = [d["value"] for d in sorted(rhr, key=lambda r: r["calendarDate"])[-30:]]
        self.assertEqual(baselines["rhr_30d"], round(sum(newest_thirty) / 30, 1))
        self.assertEqual(baselines["rhr_yearly"]["2026"], round(sum(d["value"] for d in rhr) / len(rhr), 1))
        self.assertEqual(baselines["rhr_total_days"], 200)
        self.assertEqual(baselines["rhr_months_tracked"], len(multi_year))
        # The chart labels each monthly point with its month and year.
        self.assertTrue(all(m["year"] == m["month"][:4] for m in multi_year))

    def test_reports_tracked_day_count_for_the_ui(self):
        baselines, _ = analytics.calculate_baselines(make_rhr("2026-08-01", 40), make_hrv("2026-08-01", 40), [make_sleep("2026-08-01")])
        self.assertEqual(baselines["rhr_total_days"], 40)

    def test_empty_inputs_use_documented_defaults_instead_of_crashing(self):
        baselines, multi_year = analytics.calculate_baselines([], [], [])
        self.assertEqual(multi_year, [])
        self.assertEqual(baselines["rhr_30d"], 50.0)
        self.assertEqual(baselines["hrv_30d"], 56.0)

    def test_latest_record_ignores_input_order(self):
        records = make_rhr("2026-08-01", 5)
        self.assertEqual(analytics.latest_record(records, "calendarDate"), records[-1])
        self.assertEqual(analytics.latest_record(records[::-1], "calendarDate"), records[-1])
        self.assertEqual(analytics.latest_record([], "calendarDate"), {})


# ---------------------------------------------------------------------------
# Circadian architecture
# ---------------------------------------------------------------------------

class CircadianTests(unittest.TestCase):
    def test_regular_bedtimes_score_full_alignment(self):
        sleep = [make_sleep(f"2026-09-{day:02d}", bedtime=1380) for day in range(1, 15)]
        circadian = analytics.compute_circadian_architecture(sleep)
        self.assertTrue(circadian["available"])
        self.assertEqual(circadian["alignment_pct"], 100)
        self.assertEqual(circadian["onset_median_clock"], "11:00 PM")

    def test_irregular_bedtimes_reduce_alignment(self):
        bedtimes = [1320, 1440, 1330, 1470, 1340, 1500, 1350, 1420]
        sleep = [make_sleep(f"2026-09-{day:02d}", bedtime=bed) for day, bed in enumerate(bedtimes, start=1)]
        circadian = analytics.compute_circadian_architecture(sleep)
        self.assertTrue(circadian["available"])
        self.assertLess(circadian["alignment_pct"], 100)
        self.assertGreater(circadian["onset_std_dev_minutes"], 0)
        self.assertGreaterEqual(circadian["alignment_pct"], 0)

    def test_missing_bedtimes_are_reported_as_unavailable_not_guessed(self):
        sleep = [make_sleep(f"2026-09-{day:02d}") for day in range(1, 10)]
        circadian = analytics.compute_circadian_architecture(sleep)
        self.assertFalse(circadian["available"])
        self.assertIsNone(circadian["alignment_pct"])

    def test_melatonin_window_precedes_median_onset(self):
        sleep = [make_sleep(f"2026-09-{day:02d}", bedtime=1380) for day in range(1, 15)]
        circadian = analytics.compute_circadian_architecture(sleep)
        start, end = circadian["melatonin_window_minutes"]
        self.assertEqual(start, 1380 - 45)
        self.assertEqual(end, 1380 - 15)
        self.assertEqual(circadian["melatonin_window"], "10:15 PM — 10:45 PM SGT")

    def test_window_uses_the_latest_nights_not_input_order(self):
        # Newest-first input must not silently analyse the oldest nights: an
        # unreversed list would report the 8:00 PM median of the first twelve.
        sleep = [
            make_sleep(f"2026-09-{day:02d}", bedtime=1200 if day <= 12 else 1380)
            for day in range(1, 25)
        ]
        circadian = analytics.compute_circadian_architecture(sleep[::-1])
        self.assertEqual(circadian["onset_median_clock"], "11:00 PM")


# ---------------------------------------------------------------------------
# Day strain, sleep need and the composite metrics
# ---------------------------------------------------------------------------

class StrainModelTests(unittest.TestCase):
    def setUp(self):
        self.baselines = make_baselines()
        self.fitness = make_fitness()
        self.sleep_history = [make_sleep(f"2026-09-{day:02d}", bedtime=1380) for day in range(1, 15)]

    def _strain(self, today_overrides=None, activities=None):
        today = make_today(**(today_overrides or {}))
        whoop = analytics.calculate_whoop_metrics(today, activities or [], self.sleep_history, {})
        return whoop, today

    def _signature(self, today_overrides=None, circadian=None):
        return analytics.build_garmin_signature(make_today(**(today_overrides or {})), circadian)

    def test_rest_day_is_low_and_not_pinned_at_a_floor(self):
        whoop, _ = self._strain()
        self.assertLess(whoop["day_strain"], 4.0)
        self.assertGreaterEqual(whoop["day_strain"], 0.0)

    def test_strain_rises_with_step_volume(self):
        light, _ = self._strain({"steps": 2000})
        heavy, _ = self._strain({"steps": 18000})
        self.assertLess(light["day_strain"], heavy["day_strain"])

    def test_strain_rises_with_training_sessions(self):
        session = [{"startTimeLocal": "2026-09-20 07:00:00", "duration_min": 75,
                    "aerobicTrainingEffect": 3.5, "category": "Running"}]
        without, _ = self._strain({"steps": 8000})
        with_session, _ = self._strain({"steps": 8000}, session)
        self.assertGreater(with_session["day_strain"], without["day_strain"])

    def test_strain_never_exceeds_the_scale(self):
        session = [{"startTimeLocal": "2026-09-20 07:00:00", "duration_min": 240,
                    "aerobicTrainingEffect": 5.0, "category": "Running"}] * 3
        whoop, _ = self._strain({"steps": 40000, "stress_avg": 60}, session)
        self.assertLessEqual(whoop["day_strain"], policy.DAY_STRAIN_SCALE_MAX)

    def test_yesterdays_activities_do_not_count_towards_today(self):
        stale = [{"startTimeLocal": "2026-09-19 07:00:00", "duration_min": 120,
                  "aerobicTrainingEffect": 4.0, "category": "Running"}]
        whoop, _ = self._strain({"steps": 3000}, stale)
        self.assertLess(whoop["day_strain"], 4.0)

    def test_bedtime_is_a_twelve_hour_clock(self):
        whoop, _ = self._strain()
        # The hour itself must be 1-12: a 24-hour hour with a PM suffix
        # ("22:03 PM SGT") is exactly the defect this guards against.
        self.assertRegex(whoop["recommended_bedtime"], r"^(1[0-2]|[1-9]):[0-5]\d (AM|PM) SGT$")
        self.assertEqual(whoop["recommended_wake_time"], "7:00 AM SGT")
        self.assertGreaterEqual(whoop["recommended_bedtime_minutes"], 0)
        self.assertLess(whoop["recommended_bedtime_minutes"], 24 * 60)

    def test_sleep_need_never_drops_below_the_baseline_requirement(self):
        whoop, _ = self._strain()
        self.assertGreaterEqual(whoop["total_sleep_need_hours"], 7.5)
        self.assertEqual(
            whoop["recommended_bedtime_minutes"],
            (policy.TARGET_WAKE_MINUTES - whoop["strain_sleep_need_minutes"]
             - whoop["sleep_debt_minutes"] - policy.BASELINE_SLEEP_NEED_MINUTES
             - policy.SLEEP_ONSET_ALLOWANCE_MINUTES) % (24 * 60),
        )


class GarminSignatureTests(unittest.TestCase):
    def test_measured_stress_distribution_is_used_when_present(self):
        measured = {"rest_pct": 40, "low_pct": 30, "med_pct": 20, "high_pct": 10, "samples": 480}
        signature = analytics.build_garmin_signature(make_today(stress_avg=30, stress_distribution=measured), None)
        self.assertEqual(signature["stress_distribution_source"], "measured")
        self.assertEqual(signature["stress_distribution"]["rest_pct"], 40)
        self.assertEqual(signature["stress_distribution"]["samples"], 480)

    def test_missing_stress_distribution_is_labelled_as_an_estimate(self):
        signature = analytics.build_garmin_signature(make_today(stress_avg=30), None)
        self.assertEqual(signature["stress_distribution_source"], "estimated")

    def test_body_battery_band_comes_from_policy(self):
        signature = analytics.build_garmin_signature(make_today(body_battery_charged=41), None)
        self.assertEqual(signature["body_battery_band"], "full")
        self.assertEqual(signature["body_battery_badge"], policy.BODY_BATTERY_BANDS["full"]["badge"])

    def test_circadian_values_are_passed_through_not_hard_coded(self):
        sleep = [make_sleep(f"2026-09-{day:02d}", bedtime=1380) for day in range(1, 15)]
        signature = analytics.build_garmin_signature(
            make_today(steps=9000), analytics.compute_circadian_architecture(sleep)
        )
        self.assertEqual(signature["circadian"]["circadian_alignment_pct"], 100)
        self.assertEqual(signature["circadian"]["optimal_melatonin_window"], "10:15 PM — 10:45 PM SGT")

    def test_unavailable_circadian_reports_null_rather_than_a_placeholder(self):
        signature = analytics.build_garmin_signature(make_today(steps=9000), None)
        self.assertFalse(signature["circadian"]["available"])
        self.assertIsNone(signature["circadian"]["circadian_alignment_pct"])
        self.assertIsNone(signature["circadian"]["optimal_melatonin_window"])


class CompositeScoreTests(unittest.TestCase):
    """Readiness and injury risk used to be re-derived in the browser; they now
    live with the rest of the engine, so the ladder is testable here."""

    def setUp(self):
        self.baselines = make_baselines()
        self.fitness = make_fitness()
        self.whoop = {"accumulated_7d_debt_hours": 4.7}

    def _readiness(self, recovery_score, today=None, fitness=None, whoop=None):
        return analytics.calculate_readiness(
            today or make_today(), self.baselines, fitness or self.fitness,
            whoop or self.whoop, {"recovery_score": recovery_score},
        )  # the engine's recovery score is the seed; the rest is the model


    def test_workload_balance_and_sleep_debt_move_the_score(self):
        # ACWR inside the 0.8-1.3 band and low sleep debt both add points.
        balanced = self._readiness(70, fitness=make_fitness(acwr=1.0), whoop={"accumulated_7d_debt_hours": 1.0})
        spiked = self._readiness(70, fitness=make_fitness(acwr=1.8), whoop={"accumulated_7d_debt_hours": 9.0})
        self.assertGreater(balanced["score"], spiked["score"])
        self.assertEqual(spiked["workload_band"], "Too Heavy")

    def test_band_badge_and_tone_agree_with_the_score(self):
        for score in (0, 24, 25, 49, 50, 74, 75, 100):
            with self.subTest(score=score):
                result = self._readiness(score, today=make_today(hrv_last_night=56))
                band = result["band"]
                self.assertEqual(result["badge"], policy.READINESS_BANDS[band]["badge"])
                self.assertEqual(result["tone"], policy.READINESS_BANDS[band]["tone"])

    def test_suppressed_hrv_is_reported_as_a_red_tone(self):
        result = self._readiness(70, today=make_today(hrv_last_night=40))
        self.assertEqual(result["hrv_tone"], policy.HRV_BANDS["below"]["tone"])

    def test_injury_risk_ladder_and_factors(self):
        low = analytics.calculate_injury_risk(make_today(), self.baselines, self.fitness, self.whoop, {"recovery_score": 80})
        self.assertEqual(low["band"], "low")
        self.assertEqual(low["badge"], policy.INJURY_BANDS["low"]["badge"])
        self.assertEqual(len(low["factors"]), 4)

        high = analytics.calculate_injury_risk(
            make_today(hrv_last_night=40), self.baselines, make_fitness(acwr=1.9),
            {"accumulated_7d_debt_hours": 9.0}, {"recovery_score": 20},
        )
        self.assertEqual(high["band"], "high")
        self.assertGreater(high["pct"], low["pct"])
        self.assertEqual(
            {f["label"] for f in high["factors"]},
            {"Workload Spike", "HRV Trend", "Sleep Debt", "Recovery Score"},
        )
        # Every tone must belong to the shared vocabulary: it becomes a Tailwind
        # class name in the browser, so an off-vocabulary colour renders nowhere.
        for factor in low["factors"] + high["factors"]:
            with self.subTest(factor=factor["label"]):
                self.assertIn(factor["tone"], policy.TONE_NAMES)

    def test_recovery_score_band_is_owned_by_policy(self):
        for score in (0, 33, 34, 66, 67, 100):
            with self.subTest(score=score):
                self.assertEqual(
                    policy.recovery_zone(score),
                    policy.RECOVERY_ZONE_LABELS[policy.recovery_band(score)],
                )


class TodaySnapshotTests(unittest.TestCase):
    def test_body_battery_failure_is_recorded_as_a_fallback(self):
        dq = DataQuality()
        snapshot = source.fetch_today_snapshot(
            FakeClient(body_battery=RuntimeError("no data"), summary={"totalSteps": 8123, "averageStressLevel": 22}),
            "2026-09-20", make_sleep("2026-09-20"), make_hrv("2026-09-20", 1)[0], make_rhr("2026-09-20", 1), dq,
        )
        metrics = dq.as_dict()["metrics"]
        self.assertEqual(metrics["body_battery"]["source"], "fallback")
        self.assertEqual(metrics["user_summary"]["source"], "live")
        self.assertEqual(snapshot["steps"], 8123)

    def test_missing_stress_values_do_not_shadow_real_steps(self):
        snapshot = source.fetch_today_snapshot(
            FakeClient(summary={"totalSteps": 12000, "averageStressLevel": None}),
            "2026-09-20", make_sleep("2026-09-20"), make_hrv("2026-09-20", 1)[0], make_rhr("2026-09-20", 1),
        )
        self.assertEqual(snapshot["steps"], 12000)
        self.assertEqual(snapshot["stress_avg"], 15)  # documented placeholder

    def test_measured_stress_average_overrides_the_summary_value(self):
        samples = [[i, 30] for i in range(60)]
        snapshot = source.fetch_today_snapshot(
            FakeClient(stress={"stressValuesArray": samples}, summary={"totalSteps": 8000, "averageStressLevel": 55}),
            "2026-09-20", make_sleep("2026-09-20"), make_hrv("2026-09-20", 1)[0], make_rhr("2026-09-20", 1),
        )
        self.assertEqual(snapshot["stress_avg"], 30.0)
        self.assertEqual(snapshot["stress_distribution"]["samples"], 60)


# ---------------------------------------------------------------------------
# Model output validation
# ---------------------------------------------------------------------------

class AiOutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.baselines = {"hrv_30d": 55.0, "rhr_30d": 50.0}
        self.today = {"hrv_last_night": 60.0, "rhr": 51.0, "sleep_stress": 16.0, "respiration_rate": 13.0}

    def _engine_inputs(self):
        return (
            dict(self.today, sleep_time_seconds=27000, deep_sleep_seconds=6000, rem_sleep_seconds=4500),
            dict(self.baselines, respiration_avg_30d=13.0, deep_sleep_pct_180d=22.0,
                 hrv_180d=55.0, rhr_all_time=50.0),
            {"fitness_age": 24.7},
        )

    def test_zone_is_recomputed_so_it_cannot_contradict_the_score(self):
        # The deployed vault shipped score 68 with a YELLOW zone label, so the
        # page argued with itself in two adjacent paragraphs.
        result = clinical_engine.normalize_ai_result(
            {"recovery_score": 68, "recovery_zone": "YELLOW (ADEQUATE RECOVERY)"},
            self.today, self.baselines,
        )
        self.assertEqual(result["recovery_score"], 68)
        self.assertEqual(result["recovery_zone"], policy.RECOVERY_ZONE_LABELS["green"])
        self.assertEqual(result["recovery_band"], "green")

    def test_low_score_cannot_claim_a_green_zone(self):
        result = clinical_engine.normalize_ai_result(
            {"recovery_score": 30, "recovery_zone": "GREEN (OPTIMAL RECOVERY)"},
            self.today, self.baselines,
        )
        self.assertEqual(result["recovery_zone"], policy.RECOVERY_ZONE_LABELS["red"])

    def test_scores_are_coerced_and_clamped(self):
        cases = {"72": 72, 150: 100, -5: 0, 66.6: 67}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                result = clinical_engine.normalize_ai_result({"recovery_score": raw}, self.today, self.baselines)
                self.assertEqual(result["recovery_score"], expected)

    def test_unusable_scores_are_rejected(self):
        for bad in ({"recovery_score": None}, {"recovery_score": "high"}, {}, "not a dict", None, [1, 2]):
            with self.subTest(payload=bad):
                self.assertIsNone(clinical_engine.normalize_ai_result(bad, self.today, self.baselines))

    def test_missing_numbers_fall_back_to_values_we_measured_ourselves(self):
        warning = clinical_engine.normalize_ai_result({"recovery_score": 70}, self.today, self.baselines)["illness_early_warning"]
        self.assertEqual(warning["delta_rhr_bpm"], 1.0)
        self.assertEqual(warning["delta_hrv_pct"], 9.1)
        self.assertEqual(warning["sleep_stress"], 16.0)
        self.assertEqual(warning["respiration_rate"], 13.0)
        self.assertEqual(warning["risk_level"], "LOW")

    def test_unknown_risk_level_is_not_trusted(self):
        result = clinical_engine.normalize_ai_result(
            {"recovery_score": 70, "illness_early_warning": {"risk_level": "CATASTROPHIC"}},
            self.today, self.baselines,
        )
        self.assertEqual(result["illness_early_warning"]["risk_level"], "LOW")

    def test_string_fields_are_normalised_into_lists(self):
        result = clinical_engine.normalize_ai_result(
            {"recovery_score": 70,
             "illness_early_warning": {"details": "single string"},
             "actionable_directives": "one directive"},
            self.today, self.baselines,
        )
        self.assertEqual(result["illness_early_warning"]["details"], ["single string"])
        self.assertEqual(result["actionable_directives"], ["one directive"])
        self.assertEqual(result["source"], "gemini")

    def test_directive_list_is_capped_and_stripped(self):
        result = clinical_engine.normalize_ai_result(
            {"recovery_score": 70, "actionable_directives": [f"  d{i}  " for i in range(12)]},
            self.today, self.baselines,
        )
        self.assertEqual(len(result["actionable_directives"]), 6)
        self.assertEqual(result["actionable_directives"][0], "d0")

    def test_invalid_model_output_falls_back_to_the_rule_engine(self):
        original = clinical_engine.query_gemini_api
        clinical_engine.query_gemini_api = lambda *a, **k: {"recovery_score": "not a number"}
        try:
            result = clinical_engine.synthesize(*self._engine_inputs())
        finally:
            clinical_engine.query_gemini_api = original
        self.assertEqual(result["source"], "deterministic")

    def test_valid_model_output_is_used_and_labelled(self):
        original = clinical_engine.query_gemini_api
        clinical_engine.query_gemini_api = lambda *a, **k: {
            "recovery_score": 82,
            "recovery_zone": "RED (HIGH NEUROLOGICAL STRAIN)",
            "autonomic_nervous_analysis": "ok",
        }
        try:
            result = clinical_engine.synthesize(self.today, self.baselines, {})
        finally:
            clinical_engine.query_gemini_api = original
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["recovery_zone"], policy.RECOVERY_ZONE_LABELS["green"])

    def test_rule_engine_output_is_labelled_too(self):
        original = clinical_engine.query_gemini_api
        clinical_engine.query_gemini_api = lambda *a, **k: None
        try:
            result = clinical_engine.synthesize(*self._engine_inputs())
        finally:
            clinical_engine.query_gemini_api = original
        self.assertEqual(result["source"], "deterministic")

    def test_rule_engine_verdicts_do_not_assert_unmeasured_numbers(self):
        original = clinical_engine.query_gemini_api
        clinical_engine.query_gemini_api = lambda *a, **k: None
        try:
            result = clinical_engine.synthesize(*self._engine_inputs())
        finally:
            clinical_engine.query_gemini_api = original
        # These literals used to be hard-coded even when the measurement said
        # otherwise ("Peak 5-minute HRV reached 89 ms", "+4.3 years younger").
        self.assertNotIn("89 ms", result["autonomic_nervous_analysis"])
        self.assertNotIn("+4.3", result["workload_and_biological_age"])
        self.assertIn("4.3 years", result["workload_and_biological_age"])  # 29 - 24.7, computed


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

class PayloadAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(
            stress={"stressValuesArray": [[i, 20] for i in range(60)]},
            body_battery=[{"charged": 41, "drained": 12}],
            summary={"totalSteps": 9100, "averageStressLevel": 21},
        )
        self.fetched = {
            "today_str": "2026-09-20",
            "rhr": make_rhr("2026-06-01", 120),
            "hrv": make_hrv("2026-06-01", 120),
            "sleep": [make_sleep(f"2026-0{month}-{day:02d}", bedtime=1380)
                      for month in (8, 9) for day in range(1, 11)],
            "fitness": make_fitness(),
            "activities": [],
        }

    def _dq(self, sleep_live=True):
        dq = DataQuality()
        dq.record("sleep", sleep_live, "20 nights")
        dq.record("rhr", True, "120 readings")
        dq.record("hrv", True, "120 summaries")
        return dq

    def test_payload_ships_resolved_bands_and_computed_scores(self):
        payload = sync.build_payload(self.client, self.fetched, self._dq())

        self.assertEqual(payload["policy"]["freshness"]["stale_after_hours"], policy.STALE_AFTER_HOURS)
        self.assertEqual(payload["policy"]["day_strain"]["scale_max"], policy.DAY_STRAIN_SCALE_MAX)

        readiness = payload["readiness"]
        self.assertIsInstance(readiness["score"], int)
        self.assertEqual(readiness["badge"], policy.READINESS_BANDS[readiness["band"]]["badge"])
        self.assertIn(readiness["tone"], policy.TONE_NAMES)

        injury = payload["injury_risk"]
        self.assertEqual(injury["badge"], policy.INJURY_BANDS[injury["band"]]["badge"])
        self.assertEqual(len(injury["factors"]), 4)

        intel = payload["clinical_intelligence"]
        self.assertEqual(intel["recovery_zone"], policy.recovery_zone(intel["recovery_score"]))
        self.assertEqual(intel["recovery_tone"], policy.RECOVERY_TONES[intel["recovery_band"]])

        self.assertEqual(payload["garmin_signature"]["stress_distribution_source"], "measured")
        self.assertEqual(payload["today"]["steps"], 9100)
        self.assertEqual(payload["data_quality"]["metrics"]["circadian"]["source"], "live")
        self.assertEqual(datetime.fromisoformat(payload["updated_at"]).utcoffset(), timedelta(0))

    def test_publish_gate_refuses_to_ship_fallen_back_core_metrics(self):
        with self.assertRaises(SystemExit):
            sync.build_payload(self.client, self.fetched, self._dq(sleep_live=False))


# ---------------------------------------------------------------------------
# Encryption envelope
# ---------------------------------------------------------------------------

class EncryptionTests(unittest.TestCase):
    def test_round_trip_recovers_the_plaintext(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        payload = json.dumps({"today": {"rhr": 50, "note": "uncertainty: +/- 1"}})
        envelope = encrypt_data.encrypt_payload(payload, "Capybara", 1000)
        self.assertEqual(envelope["format"], "aes-256-gcm-pbkdf2")
        self.assertEqual(envelope["iterations"], 1000)

        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=base64.b64decode(envelope["salt"]), iterations=envelope["iterations"])
        decrypted = AESGCM(kdf.derive(b"Capybara")).decrypt(
            base64.b64decode(envelope["iv"]), base64.b64decode(envelope["data"]), None
        )
        self.assertEqual(json.loads(decrypted), json.loads(payload))

    def test_wrong_passphrase_fails_authentication(self):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        envelope = encrypt_data.encrypt_payload("{}", "Capybara", 1000)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=base64.b64decode(envelope["salt"]), iterations=envelope["iterations"])
        with self.assertRaises(InvalidTag):
            AESGCM(kdf.derive(b"Barabara")).decrypt(
                base64.b64decode(envelope["iv"]), base64.b64decode(envelope["data"]), None
            )

    def test_work_factor_meets_owasp_guidance(self):
        self.assertGreaterEqual(encrypt_data.DEFAULT_ITERATIONS, 600_000)

    def test_envelope_records_the_cost_it_was_written_with(self):
        envelope = encrypt_data.encrypt_payload("{}", "Capybara", 123_456)
        self.assertEqual(envelope["iterations"], 123_456)


if __name__ == "__main__":
    unittest.main()
