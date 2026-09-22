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
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bio_analytics as analytics
import bio_coach
import bio_correlate
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


def make_profile(**overrides):
    """The account's own profile: the only source of personal facts on the page."""
    profile = {
        "gender": "MALE", "birth_date": "1996-12-07", "age_years": 29,
        "height_cm": 167.0, "weight_kg": 63.5, "vo2max": 51.2,
        "lactate_threshold_hr": 166, "activity_level": 6,
        "devices": [{"name": "vívomove Style", "primary": False},
                    {"name": "fenix 6S ASIA Sapphire", "primary": True}],
        "heat_acclimation_pct": 5, "altitude_acclimation_pct": 0,
    }
    profile.update(overrides)
    return profile


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
        self.assertEqual(spiked["workload_band"], "Danger Zone")

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


class RhrTierTests(unittest.TestCase):
    """The RHR badge follows the number; the markup used to hard-code ATHLETIC.

    A 45 bpm or a 92 bpm reading displayed the same fixed label, the same defect
    class as the static LIVE pill: a claim on screen with no owner behind it.
    """

    def test_boundaries_match_the_ladder_the_panel_documents(self):
        for value, expected in (
            (38, "elite"), (45, "elite"), (48, "elite"),
            (49, "athletic"), (55, "athletic"), (58, "athletic"),
            (59, "average"), (70, "average"), (80, "average"),
            (81, "elevated"), (95, "elevated"),
        ):
            with self.subTest(value=value):
                self.assertEqual(policy.rhr_tier(value), expected)

    def test_every_tier_carries_a_badge_and_a_policy_tone(self):
        for key in ("elite", "athletic", "average", "elevated"):
            tier = policy.RHR_TIERS[key]
            self.assertTrue(tier["badge"])
            self.assertIn(tier["tone"], policy.TONE_NAMES)

    def test_the_snapshot_publishes_the_tier_matching_its_own_reading(self):
        for value, expected in ((45.0, "elite"), (52.0, "athletic"), (62.0, "average"), (90.0, "elevated")):
            with self.subTest(rhr=value):
                snapshot = source.fetch_today_snapshot(
                    FakeClient(summary={"totalSteps": 8000, "averageStressLevel": 20}),
                    "2026-09-20", make_sleep("2026-09-20"), make_hrv("2026-09-20", 1)[0],
                    make_rhr("2026-09-20", 1, value=value),
                )
                self.assertEqual(snapshot["rhr"], value)
                self.assertEqual(snapshot["rhr_tier"], expected)
                self.assertEqual(snapshot["rhr_tier_label"], policy.RHR_TIERS[expected]["badge"])
                self.assertEqual(snapshot["rhr_tier_tone"], policy.RHR_TIERS[expected]["tone"])


class AcwrBandTests(unittest.TestCase):
    """One set of ACWR band names, in the guide's words, on every surface.

    The badge used to show Garmin's word (LOW) while the readiness card showed
    policy's old ladder (Very Light) for the identical ratio -- two vocabularies,
    neither of them the one the user's own guide documents.
    """

    def test_the_four_bands_use_the_guides_words(self):
        self.assertEqual(policy.acwr_workload_band(0.4), "Fresh / Under-trained")
        self.assertEqual(policy.acwr_workload_band(1.0), "Sweet Spot")
        self.assertEqual(policy.acwr_workload_band(1.4), "High")
        self.assertEqual(policy.acwr_workload_band(1.9), "Danger Zone")

    def test_band_boundaries_follow_the_guides_numbers(self):
        for acwr, band in ((0.79, "under"), (0.8, "sweet"), (1.3, "sweet"),
                           (1.31, "high"), (1.5, "high"), (1.51, "danger")):
            with self.subTest(acwr=acwr):
                self.assertEqual(policy.acwr_band(acwr), band)

    def test_every_band_carries_a_label_a_policy_tone_and_a_plain_meaning(self):
        labels = []
        for band, entry in policy.ACWR_BANDS.items():
            with self.subTest(band=band):
                self.assertIn(entry["tone"], policy.TONE_NAMES)
                self.assertTrue(entry["label"])
                self.assertTrue(entry["plain"])
                labels.append(entry["label"])
        self.assertEqual(len(labels), len(set(labels)))

    def test_an_unmeasured_ratio_publishes_no_band(self):
        self.assertEqual(sync.acwr_band_fields({}), {})
        self.assertEqual(sync.acwr_band_fields({"acwr": None}), {})

    def test_the_snapshot_keeps_garmins_hrv_word_as_context_only(self):
        """HRV used to carry a second, word-keyed meaning alongside the band."""
        hrv_record = {**make_hrv("2026-09-20", 1)[0], "status": "UNBALANCED"}
        snapshot = source.fetch_today_snapshot(
            FakeClient(summary={"totalSteps": 8000, "averageStressLevel": 20}),
            "2026-09-20", make_sleep("2026-09-20"), hrv_record,
            make_rhr("2026-09-20", 1),
        )
        self.assertEqual(snapshot["hrv_status"], "UNBALANCED")
        self.assertNotIn("hrv_status_tone", snapshot)


class HrvSingleMeaningTests(unittest.TestCase):
    """The badge, the Autonomic row, the pillar dot and the quadrant all read the band.

    Before this, one 52 ms reading was shown as "BALANCED" in green by the badge and
    as below-baseline amber by every other surface, because the badge keyed on
    Garmin's own status word instead of the athlete's 30-day baseline.
    """

    def _hrv_pillar(self, hrv, baseline):
        fitbit = analytics.calculate_fitbit_metrics(
            make_today(hrv_last_night=hrv), make_baselines(hrv_30d=baseline), make_fitness()
        )
        return [p for p in fitbit["health_metrics_5_pillars"] if p["key"] == "hrv"][0]

    def test_every_band_carries_a_label_from_the_tone_vocabulary(self):
        for band, entry in policy.HRV_BANDS.items():
            with self.subTest(band=band):
                self.assertIn(entry["tone"], policy.TONE_NAMES)
                self.assertTrue(entry["label"])
                self.assertTrue(entry["plain"])

    def test_the_pillar_publishes_the_band_the_reading_implies(self):
        for hrv, band in ((60.0, "above"), (52.0, "near"), (40.0, "below")):
            with self.subTest(hrv=hrv):
                pillar = self._hrv_pillar(hrv, 56.0)
                self.assertEqual(pillar["status"], policy.HRV_BANDS[band]["label"])
                self.assertEqual(pillar["status_color"], policy.HRV_BANDS[band]["tone"])

    def test_a_reading_below_baseline_can_never_read_resilient(self):
        pillar = self._hrv_pillar(52.0, 56.0)
        self.assertNotEqual(pillar["status"], policy.HRV_BANDS["above"]["label"])
        self.assertEqual(pillar["status_color"], "amber")

    def test_one_band_cannot_carry_two_different_labels(self):
        labels = [entry["label"] for entry in policy.HRV_BANDS.values()]
        self.assertEqual(len(labels), len(set(labels)))

    # --- the chart's scrub HUD ------------------------------------------------

    def _night_series(self, final_hrv):
        """60 steady nights plus one final reading, as Garmin returns them."""
        history = make_hrv("2026-07-01", 60, value=56.0)
        history.append({"calendarDate": "2026-08-30", "lastNightAvg": final_hrv,
                        "weeklyAvg": 57.4, "status": "BALANCED"})
        return history

    def test_a_nights_published_band_sits_on_the_cards_own_basis(self):
        """The HUD keyed on a 7-day average while the card keyed on the 30-day.

        That is how one 52 ms night read "BALANCED (-8.8%)" in the chart's HUD and
        "Below Baseline (-5.6%)" on the card 40 lines away. Each night now ships the
        band for that night, on the baseline the card reads, so the two agree.
        """
        history = self._night_series(52.0)
        baselines, _ = analytics.calculate_baselines(make_rhr("2026-07-01", 60), history, [])
        night = analytics.hrv_night_bands(history)["2026-08-30"]

        self.assertEqual(night["baseline"], baselines["hrv_30d"])
        self.assertEqual(
            night["delta_pct"],
            round((52.0 - baselines["hrv_30d"]) / baselines["hrv_30d"] * 100, 1),
        )
        expected_band = policy.hrv_band(52.0, baselines["hrv_30d"])
        self.assertEqual(night["band"], expected_band)
        self.assertEqual(night["band_label"], policy.HRV_BANDS[expected_band]["label"])
        self.assertEqual(night["tone"], policy.HRV_BANDS[expected_band]["tone"])

    def test_every_night_carries_one_label_and_one_tone(self):
        for final_hrv, band in ((70.0, "above"), (52.0, "near"), (44.0, "below")):
            with self.subTest(final_hrv=final_hrv):
                night = analytics.hrv_night_bands(self._night_series(final_hrv))["2026-08-30"]
                self.assertEqual(night["band"], band)
                self.assertIn(night["tone"], policy.TONE_NAMES)
                self.assertEqual(night["band_label"], policy.HRV_BANDS[band]["label"])

    def test_a_night_without_a_measurement_publishes_no_band(self):
        """Absent must stay absent: the HUD defaulted to "BALANCED" instead."""
        self.assertEqual(analytics.hrv_night_bands([{"calendarDate": "2026-09-01"}]), {})
        self.assertEqual(analytics.hrv_night_bands([]), {})
        self.assertEqual(policy.hrv_band_fields(None, 56.0), {})
        self.assertEqual(policy.hrv_band_fields(52.0, 0), {})

    def test_the_scrub_hud_reads_the_published_band_not_garmins_word(self):
        """The last surface that gave one reading two verdicts.

        The HUD printed `item.status || 'BALANCED'` beside a chip whose emerald was
        fixed in markup, so a below-baseline night could read "BALANCED" in green
        next to a card reading "Below Baseline" in amber.
        """
        page = (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )
        hud_bar = page.split("function scrubHrvCallout", 1)[1].split("function getHrvCalloutHtml", 1)[0]
        callout = page.split("function getHrvCalloutHtml", 1)[1].split("function renderScatterMatrix", 1)[0]

        # Neither scrub surface may read Garmin's word or invent a reading.
        self.assertNotIn("item.status", page)
        for surface in (hud_bar, callout):
            with self.subTest(surface=surface[:44]):
                self.assertNotIn("BALANCED", surface)
                self.assertNotIn("|| 60", surface)
                self.assertNotIn("|| 57", surface)
                self.assertIn("hrvVerdictChip(item)", surface)
        # The callout's chip takes its colour from that one verdict, and the HUD's
        # chip ships as an absent value with no tone of its own.
        self.assertIn("${chip.cls}", callout)
        hud_chip = page.split('id="hrvHudStatus"', 1)[1].split("</span>", 1)[0]
        self.assertTrue(hud_chip.rstrip().endswith(">--"), hud_chip)
        self.assertNotIn("emerald", hud_chip)


# ---------------------------------------------------------------------------
# Interactive instruments: the picture draws policy's bands, never its own
# ---------------------------------------------------------------------------

class InstrumentTests(unittest.TestCase):
    """The dial, the ring and the grid lay out the engine's numbers and nothing else.

    A dial is the easiest place to smuggle a threshold into the browser, so the
    edges, the tones and the plain-English meanings all ship from policy, and an
    unmeasured reading has to render as absent rather than as a plausible number.
    """

    def _page(self):
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    def test_the_band_edges_are_contiguous_and_cover_the_dial(self):
        ranges = policy.acwr_band_ranges()
        self.assertEqual([r["key"] for r in ranges], ["under", "sweet", "high", "danger"])
        self.assertEqual(ranges[0]["from"], 0.0)
        self.assertEqual(ranges[-1]["to"], policy.ACWR_DIAL_MAX)
        for left, right in zip(ranges, ranges[1:]):
            with self.subTest(edge=left["to"]):
                self.assertEqual(left["to"], right["from"])

    def test_the_drawn_band_is_the_band_the_verdict_uses(self):
        for entry in policy.acwr_band_ranges():
            with self.subTest(band=entry["key"]):
                inside = (entry["from"] + entry["to"]) / 2
                self.assertEqual(policy.acwr_band(inside), entry["key"])
                self.assertIn(entry["tone"], policy.TONE_NAMES)
                self.assertTrue(entry["label"])
                self.assertTrue(entry["plain"])
        # A ratio sitting exactly on an edge reads as the band that edge closes,
        # which is the arc the dial has just drawn under it.
        self.assertEqual(policy.acwr_band(policy.ACWR_SWEET_MIN), "sweet")
        self.assertEqual(policy.acwr_band(policy.ACWR_SWEET_MAX), "sweet")
        self.assertEqual(policy.acwr_band(policy.ACWR_SAFE_MAX), "high")
        self.assertEqual(policy.acwr_band(policy.ACWR_DIAL_MAX), "danger")

    def test_the_snapshot_ships_the_scale_and_the_bands(self):
        dial = policy.policy_snapshot()["acwr"]
        self.assertEqual(dial["scale_max"], policy.ACWR_DIAL_MAX)
        self.assertEqual(dial["bands"], policy.acwr_band_ranges())

    def test_the_browser_holds_no_workload_threshold_of_its_own(self):
        page = self._page()
        dial = page.split("function acwrDialBands", 1)[1].split("function renderMovementRing", 1)[0]
        for literal in ("0.8", "1.3", "1.5"):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, dial)
        # The band edges and their meanings come from the payload's policy block.
        self.assertIn("globalBioData.policy", dial)

    def test_every_instrument_reads_absent_until_the_engine_speaks(self):
        page = self._page()
        for element in ('id="acwrDialValue"', 'id="acwrDialBand"', 'id="movementRingPct"', 'id="consistencySummary"'):
            with self.subTest(element=element):
                tag = page.split(element, 1)[1].split("</", 1)[0]
                self.assertTrue(tag.rstrip().endswith("--"), tag)
        # Each instrument has an explicit absent path rather than a zero reading.
        for name, marker in (
            ("renderMovementTrend", "No finished day has a step record yet"),
            ("renderConsistencyGrid", "No session has been logged in this window"),
            ("renderAcwrDial", "so the dial has nothing to point at"),
        ):
            with self.subTest(function=name):
                body = page.split(f"function {name}", 1)[1].split("\n    function ", 1)[0]
                self.assertTrue(marker in body or marker in page, marker)


# ---------------------------------------------------------------------------
# Score ownership: the rules score, the model narrates
# ---------------------------------------------------------------------------

class ScoreOwnershipTests(unittest.TestCase):
    """The published verdict must not depend on which engine answered.

    Byte-identical inputs once published recovery 94% GREEN PRIME locally (no
    key, rule engine) and 62% YELLOW READY on the runner (real key, model-
    authored) -- a 32-point swing that changed the training verdict. The rule
    engine now owns the recovery score and everything derived from it; Gemini
    supplies prose, and its own number is kept as `model_score` so the two can
    be compared rather than silently swapped.
    """

    # Deliberately contrarian: numbers and labels that disagree with the rule
    # engine's read of the same measurements.
    MODEL_REPLY = {
        "recovery_score": 62,
        "recovery_zone": "YELLOW (ADEQUATE RECOVERY)",
        "illness_early_warning": {
            "risk_level": "HIGH",
            "status_headline": "Model thinks you are falling ill",
            "details": ["model illness prose"],
        },
        "autonomic_nervous_analysis": "model autonomic prose",
        "sleep_architecture_analysis": "model sleep prose",
        "workload_and_biological_age": "model workload prose",
        "actionable_directives": ["model directive a", "model directive b", "model directive c"],
    }

    def setUp(self):
        self.baselines = {"hrv_30d": 55.0, "rhr_30d": 50.0}
        self.today = {"hrv_last_night": 60.0, "rhr": 51.0, "sleep_stress": 16.0, "respiration_rate": 13.0}

    def test_gemini_has_a_supported_ordered_fallback_chain(self):
        self.assertEqual(
            list(clinical_engine.GEMINI_MODELS),
            ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"],
        )
        self.assertEqual(len(clinical_engine.GEMINI_MODELS), len(set(clinical_engine.GEMINI_MODELS)))

    def _engine_inputs(self):
        return (
            dict(self.today, sleep_time_seconds=27000, deep_sleep_seconds=6000, rem_sleep_seconds=4500),
            dict(self.baselines, respiration_avg_30d=13.0, deep_sleep_pct_180d=22.0,
                 hrv_180d=55.0, rhr_all_time=50.0),
            # A measured ratio, because the paragraph names its band: an unmeasured
            # one is reported as unmeasured rather than assumed to be 0.2 (see
            # UnmeasuredWorkloadTests).
            {"fitness_age": 24.7, "acute_load": 40, "chronic_load": 200, "acwr": 0.2},
        )

    def _synthesize(self, model_reply, **today_overrides):
        """Run the real engine with the model stubbed to `model_reply` (None == no key)."""
        today, baselines, fitness = self._engine_inputs()
        today.update(today_overrides)
        original = clinical_engine.query_gemini_api
        clinical_engine.query_gemini_api = lambda *a, **k: model_reply
        try:
            return clinical_engine.synthesize(today, baselines, fitness)
        finally:
            clinical_engine.query_gemini_api = original

    def _plain_tails(self, result):
        """The explanation paragraph following each clinical paragraph."""
        return {
            key: result[key].split(clinical_engine.PLAIN_PARAGRAPH_SEPARATOR)[-1].strip()
            for key in clinical_engine.NARRATIVE_KEYS
        }

    def test_published_verdict_is_identical_with_and_without_a_model(self):
        without = self._synthesize(None)
        with_model = self._synthesize(self.MODEL_REPLY)

        for key in ("recovery_score", "recovery_band", "recovery_tone", "recovery_zone",
                    "recovery_briefing", "illness_early_warning", "score_source"):
            with self.subTest(key=key):
                self.assertEqual(without[key], with_model[key])
        self.assertEqual(
            with_model["actionable_directives"][0],
            without["actionable_directives"][0],
        )

    def test_the_models_number_is_recorded_but_never_published(self):
        rules_score = self._synthesize(None)["recovery_score"]
        result = self._synthesize(self.MODEL_REPLY)

        self.assertEqual(result["recovery_score"], rules_score)
        self.assertNotEqual(result["recovery_score"], 62)
        self.assertEqual(result["model_score"], 62)
        self.assertEqual(result["score_source"], "deterministic")
        self.assertEqual(result["narrative_source"], "gemini")

    def test_a_model_score_of_68_can_no_longer_ship_with_a_yellow_zone(self):
        # The deployed vault shipped exactly that contradictory pair.
        result = self._synthesize({"recovery_score": 68, "autonomic_nervous_analysis": "prose"})

        self.assertEqual(result["recovery_zone"], policy.recovery_zone(result["recovery_score"]))
        self.assertEqual(result["recovery_band"], policy.recovery_band(result["recovery_score"]))
        self.assertEqual(result["model_score"], 68)

    def test_model_labels_never_reach_the_payload(self):
        result = self._synthesize(self.MODEL_REPLY)

        self.assertEqual(result["recovery_zone"], policy.recovery_zone(result["recovery_score"]))
        self.assertNotEqual(result["recovery_zone"], "YELLOW (ADEQUATE RECOVERY)")
        self.assertNotEqual(result["illness_early_warning"]["risk_level"], "HIGH")
        self.assertNotIn("falling ill", json.dumps(result))

    def test_illness_risk_and_tone_come_from_policy(self):
        warning = self._synthesize(None)["illness_early_warning"]

        self.assertEqual(warning["risk_tone"], policy.RISK_TONES[warning["risk_level"]])

    def test_output_without_any_prose_is_rejected(self):
        for reply in ({}, {"recovery_score": 88}, {"autonomic_nervous_analysis": "   "}, "not a dict", [1, 2]):
            with self.subTest(reply=reply):
                result = self._synthesize(reply)

                self.assertEqual(result["narrative_source"], "deterministic")
                self.assertNotIn("model_score", result)

    def test_paragraphs_fall_back_to_the_measured_verdict_where_the_model_omits_them(self):
        without = self._synthesize(None)
        result = self._synthesize({"autonomic_nervous_analysis": "model autonomic prose"})

        self.assertTrue(result["autonomic_nervous_analysis"].startswith("model autonomic prose"))
        self.assertEqual(result["sleep_architecture_analysis"], without["sleep_architecture_analysis"])
        self.assertEqual(result["workload_and_biological_age"], without["workload_and_biological_age"])
        self.assertEqual(result["actionable_directives"], without["actionable_directives"])

    def test_the_training_target_directive_stays_rule_owned(self):
        without = self._synthesize(None)
        result = self._synthesize(self.MODEL_REPLY)

        self.assertEqual(result["actionable_directives"][0], without["actionable_directives"][0])
        self.assertEqual(
            result["actionable_directives"][1:],
            ["model directive a", "model directive b", "model directive c"],
        )

    def test_directive_list_is_capped_and_stripped(self):
        result = self._synthesize({"actionable_directives": [f"  d{i}  " for i in range(12)]})

        # One rule-owned training target, then at most three narrative directives.
        self.assertEqual(len(result["actionable_directives"]), 4)
        self.assertEqual(result["actionable_directives"][1], "d0")
        self.assertEqual(result["actionable_directives"][3], "d2")

    def test_a_single_string_directive_is_accepted(self):
        result = self._synthesize({"actionable_directives": "one directive"})

        self.assertEqual(result["actionable_directives"][1], "one directive")

    def test_model_score_is_coerced_and_clamped_before_it_is_recorded(self):
        for raw, expected in {"72": 72, 150: 100, -5: 0, 66.6: 67}.items():
            with self.subTest(raw=raw):
                result = self._synthesize({"recovery_score": raw, "autonomic_nervous_analysis": "prose"})

                self.assertEqual(result["model_score"], expected)

    def test_unusable_model_score_is_omitted_rather_than_guessed(self):
        result = self._synthesize({"recovery_score": "high", "autonomic_nervous_analysis": "prose"})

        self.assertNotIn("model_score", result)
        self.assertEqual(result["narrative_source"], "gemini")

    def test_every_analysis_paragraph_is_followed_by_an_explanation(self):
        for reply in (None, self.MODEL_REPLY):
            with self.subTest(with_model=bool(reply)):
                result = self._synthesize(reply)
                for key in clinical_engine.NARRATIVE_KEYS:
                    with self.subTest(key=key):
                        clinical, _, explanation = result[key].partition(
                            clinical_engine.PLAIN_PARAGRAPH_SEPARATOR
                        )
                        self.assertTrue(clinical.strip())
                        self.assertTrue(explanation.strip())
                        self.assertTrue(result[key].rstrip().endswith("."))
                        self.assertGreater(len(explanation.strip()), 20)
                        # No label: the explanation is simply the second paragraph.
                        self.assertNotIn("In plain English", result[key])

    def test_the_model_cannot_replace_the_explanation_paragraph(self):
        without = self._synthesize(None)
        with_model = self._synthesize(self.MODEL_REPLY)

        self.assertTrue(with_model["autonomic_nervous_analysis"].startswith("model autonomic prose"))
        self.assertEqual(self._plain_tails(with_model), self._plain_tails(without))
        self.assertNotIn("model", self._plain_tails(with_model)["autonomic_nervous_analysis"])

    def test_the_explanation_paragraph_restates_this_runs_measurements(self):
        recovered = self._synthesize(None, hrv_last_night=60.0)
        suppressed = self._synthesize(None, hrv_last_night=40.0)

        # The numbers and the band's own wording, not a fixed sentence that could
        # keep describing a reading the run did not take.
        self.assertIn("60 ms", recovered["autonomic_nervous_analysis"])
        self.assertIn("40 ms", suppressed["autonomic_nervous_analysis"])
        self.assertIn(policy.HRV_BANDS["above"]["plain"], recovered["autonomic_nervous_analysis"])
        self.assertIn(policy.HRV_BANDS["below"]["plain"], suppressed["autonomic_nervous_analysis"])
        self.assertIn(
            policy.ACWR_BANDS["under"]["plain"],
            recovered["workload_and_biological_age"],
        )
        self.assertNotIn("plain_english", recovered)

    def test_rule_engine_verdicts_do_not_assert_unmeasured_numbers(self):
        result = self._synthesize(None)

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
            "profile": make_profile(),
            "rhr": make_rhr("2026-06-01", 120),
            "hrv": make_hrv("2026-06-01", 120),
            "sleep": [make_sleep(f"2026-0{month}-{day:02d}", bedtime=1380)
                      for month in (8, 9) for day in range(1, 11)],
            "fitness": make_fitness(),
            "activities": [],
            # The measured signal groups: sparse blood oxygen, one located session
            # with its own weather, today's movement totals and the device's
            # forecasts.
            "location": {
                "location_days": [
                    {"date": "2026-09-18", "location": "Singapore", "latitude": 1.38, "longitude": 103.74},
                    {"date": "2026-09-19", "location": "Singapore", "latitude": 1.38, "longitude": 103.74},
                    {"date": "2026-09-20", "location": "Quan 1", "latitude": 10.77, "longitude": 106.7},
                ],
                "weather": {
                    "temp_c": 27.2,
                    "feels_like_c": 30.6,
                    "humidity_pct": 89,
                    "dew_point_c": 25.0,
                    "station": "Seletar Airport",
                },
                "weather_live": True,
            },
            "spo2": [
                {"date": "2026-09-01", "latest": 96, "sleep_average": 95.0, "lowest": 92,
                 "latest_time_local": "2026-09-01T20:31:00.0"},
                {"date": "2026-09-08", "latest": 94, "sleep_average": None, "lowest": None,
                 "latest_time_local": "2026-09-08T21:02:00.0"},
            ],
            "steps": [{"calendarDate": f"2026-09-{day:02d}", "totalSteps": 9000, "stepGoal": 10000}
                      for day in range(1, 21)],
            "hydration": {"goal_ml": 2915.0, "intake_ml": 0.0, "sweat_loss_ml": 786.0},
            "daily_activity": {"steps": 9100, "step_goal": 10000, "floors": 11.0,
                               "active_kcal": 339.0, "total_kcal": 1933.0},
            "intensity": {"weekly_total": 20, "moderate": 20, "vigorous": 0, "goal": 150},
            "races": {"date": "2026-09-20", "time5k": 1553, "time10k": 3295,
                      "time_half": 7390, "time_marathon": 16197},
        }

    def _dq(self, sleep_live=True):
        dq = DataQuality()
        dq.record("sleep", sleep_live, "20 nights")
        dq.record("rhr", True, "120 readings")
        dq.record("hrv", True, "120 summaries")
        return dq

    def test_a_failed_training_status_endpoint_does_not_stop_the_payload(self):
        self.fetched["fitness"] = make_fitness(
            acute_load=None, chronic_load=None, acwr=None, acwr_status=None
        )

        payload = sync.build_payload(self.client, self.fetched, self._dq())

        self.assertIsInstance(payload["fitbit"]["daily_readiness_score"], int)
        # The ratio is unmeasured, so no band travels with it: the card reads "--".
        self.assertNotIn("acwr_band", payload["fitness"])
        self.assertNotIn("None", payload["clinical_intelligence"]["workload_and_biological_age"])

    def test_the_payload_carries_the_top_strip(self):
        payload = sync.build_payload(self.client, self.fetched, self._dq())
        summary = payload["today_summary"]

        self.assertEqual(summary["movement"]["sessions"], 0)  # the fixture logs no activity
        self.assertEqual(summary["sleep"]["baseline_hours"], payload["whoop"]["baseline_sleep_need_hours"])
        self.assertTrue(summary["window_label"].endswith("SGT"))

    def test_the_payload_carries_the_movement_trend(self):
        payload = sync.build_payload(self.client, self.fetched, self._dq())
        trend = payload["capacity"]["trend"]

        # Twenty measured days at 9,000 against a 10,000 goal: the card reads the week
        # and the month, not the day, because the day is on the strip at the top.
        self.assertTrue(trend["available"])
        self.assertEqual(trend["week"]["mean_steps"], 9000)
        self.assertEqual(trend["week"]["days_met_goal"], 0)
        self.assertEqual(trend["month"]["mean_steps"], 9000)
        self.assertIn("9,000 steps", trend["plain"])
        # Every measured day in this fixture is identical, so there is no busier and
        # quieter side to compare: the heart line stays absent rather than splitting
        # one number into two.
        self.assertIsNone(trend["heart"])

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
        self.assertEqual(
            payload["today"]["rhr_tier_label"],
            policy.RHR_TIERS[payload["today"]["rhr_tier"]]["badge"],
        )
        # HRV ships Garmin's word as context and exactly one band-derived meaning,
        # which the pillar (and therefore the badge and the Autonomic row) reads.
        self.assertIn("hrv_status", payload["today"])
        self.assertNotIn("hrv_status_tone", payload["today"])
        hrv_pillar = [p for p in payload["fitbit"]["health_metrics_5_pillars"] if p["key"] == "hrv"][0]
        expected_band = policy.hrv_band(payload["today"]["hrv_last_night"], payload["baselines"]["hrv_30d"])
        self.assertEqual(hrv_pillar["status"], policy.HRV_BANDS[expected_band]["label"])
        self.assertEqual(hrv_pillar["status_color"], policy.HRV_BANDS[expected_band]["tone"])
        # Every night in the chart carries its own band too, resolved on that same
        # 30-night basis, so the scrub HUD cannot give a night a second verdict.
        nights = payload["history"]["daily_hrv"]
        self.assertTrue(nights)
        last_night = nights[-1]
        self.assertEqual(last_night["baseline"], payload["baselines"]["hrv_30d"])
        night_band = policy.hrv_band(last_night["lastNightAvg"], last_night["baseline"])
        self.assertEqual(last_night["band_label"], policy.HRV_BANDS[night_band]["label"])
        self.assertEqual(last_night["tone"], policy.HRV_BANDS[night_band]["tone"])
        # One band, one name, on both surfaces that describe the ratio, while
        # Garmin's own word ships as context for the ACWR panel.
        acwr = payload["fitness"]["acwr"]
        expected_band = policy.acwr_band(acwr)
        self.assertEqual(payload["fitness"]["acwr_band_label"], policy.ACWR_BANDS[expected_band]["label"])
        self.assertEqual(payload["fitness"]["acwr_band_tone"], policy.ACWR_BANDS[expected_band]["tone"])
        self.assertEqual(payload["readiness"]["workload_band"], payload["fitness"]["acwr_band_label"])
        self.assertIn("acwr_status", payload["fitness"])
        self.assertNotIn("acwr_status_tone", payload["fitness"])
        self.assertEqual(payload["data_quality"]["metrics"]["circadian"]["source"], "live")
        self.assertEqual(datetime.fromisoformat(payload["updated_at"]).utcoffset(), timedelta(0))

    def test_every_published_analysis_paragraph_carries_its_explanation(self):
        """The reader's takeaway ships inside the paragraph, not as a spare field."""
        intel = sync.build_payload(self.client, self.fetched, self._dq())["clinical_intelligence"]

        for key in clinical_engine.NARRATIVE_KEYS:
            with self.subTest(key=key):
                self.assertIn(clinical_engine.PLAIN_PARAGRAPH_SEPARATOR, intel[key])
        self.assertNotIn("plain_english", intel)

    def test_publish_gate_refuses_to_ship_fallen_back_core_metrics(self):
        with self.assertRaises(SystemExit):
            sync.build_payload(self.client, self.fetched, self._dq(sleep_live=False))

    def test_readiness_and_injury_risk_are_identical_with_and_without_a_model(self):
        """Same physiology, same published verdict -- the point of score ownership."""
        original = clinical_engine.query_gemini_api
        try:
            clinical_engine.query_gemini_api = lambda *a, **k: None
            without = sync.build_payload(self.client, self.fetched, self._dq())
            clinical_engine.query_gemini_api = lambda *a, **k: {
                "recovery_score": 62,
                "recovery_zone": "YELLOW (ADEQUATE RECOVERY)",
                "illness_early_warning": {"risk_level": "HIGH", "status_headline": "Model"},
                "autonomic_nervous_analysis": "model autonomic prose",
                "sleep_architecture_analysis": "model sleep prose",
                "workload_and_biological_age": "model workload prose",
                "actionable_directives": ["model a", "model b", "model c"],
            }
            with_model = sync.build_payload(self.client, self.fetched, self._dq())
        finally:
            clinical_engine.query_gemini_api = original

        self.assertEqual(
            with_model["clinical_intelligence"]["recovery_score"],
            without["clinical_intelligence"]["recovery_score"],
        )
        self.assertEqual(
            with_model["clinical_intelligence"]["recovery_zone"],
            without["clinical_intelligence"]["recovery_zone"],
        )
        self.assertEqual(with_model["readiness"], without["readiness"])
        self.assertEqual(with_model["injury_risk"], without["injury_risk"])
        self.assertEqual(with_model["whoop"]["strain_zone"], without["whoop"]["strain_zone"])
        self.assertEqual(with_model["clinical_intelligence"]["narrative_source"], "gemini")
        self.assertEqual(with_model["clinical_intelligence"]["model_score"], 62)


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


# ---------------------------------------------------------------------------
# Measured signal groups: oxygen, terrain, capacity
# ---------------------------------------------------------------------------

class OxygenSignalTests(unittest.TestCase):
    """Blood oxygen is an on-demand sensor, so coverage is part of the reading.

    Garmin returned a value on 13 of 61 sampled days for this account. A card
    showing one number and no coverage line would read as a nightly average, which
    is exactly what the sparse series is not.
    """

    def test_coverage_is_published_beside_the_reading(self):
        signal = analytics.build_oxygen_signal([
            {"date": "2026-09-01", "latest": 96, "sleep_average": 95.0, "lowest": 92,
             "latest_time_local": "2026-09-01T20:31:00.0"},
            {"date": "2026-09-08", "latest": 94, "sleep_average": None, "lowest": None,
             "latest_time_local": "2026-09-08T21:02:00.0"},
        ])

        self.assertEqual(signal["latest"], 94)
        self.assertEqual(signal["days_recorded"], 2)
        self.assertEqual(signal["window_days"], policy.SPO2_LOOKBACK_DAYS)
        self.assertEqual(signal["coverage_pct"], round(2 / policy.SPO2_LOOKBACK_DAYS * 100))
        self.assertEqual(signal["latest_clock"], "9:02 PM SGT")

    def test_a_single_spot_reading_is_not_presented_as_a_nightly_average(self):
        signal = analytics.build_oxygen_signal([
            {"date": "2026-09-08", "latest": 94, "sleep_average": None, "lowest": None},
        ])

        self.assertIsNone(signal["sleep_average"])
        self.assertEqual(signal["sleep_nights"], 0)
        # The band falls back to the reading that exists rather than inventing one.
        self.assertEqual(signal["band"], "mild")

    def test_no_readings_publishes_no_band_rather_than_a_placeholder(self):
        signal = analytics.build_oxygen_signal([])

        self.assertFalse(signal["available"])
        self.assertIsNone(signal["band"])
        self.assertEqual(signal["band_tone"], "slate")
        self.assertIsNone(signal["latest"])

    def test_a_low_reading_is_flagged_and_toned_by_policy(self):
        signal = analytics.build_oxygen_signal([
            {"date": "2026-05-18", "latest": 94, "sleep_average": 92.0, "lowest": 84},
        ])

        self.assertTrue(signal["dip_flagged"])
        self.assertEqual(signal["band"], policy.spo2_band(92.0))
        self.assertIn(signal["band_tone"], policy.TONE_NAMES)
        self.assertTrue(signal["plain"])


class EnvironmentSignalTests(unittest.TestCase):
    """Where training happened and what the air was doing, all device-logged."""

    def setUp(self):
        self.location_days = (
            [{"date": f"2026-09-{day:02d}", "location": "Singapore"} for day in range(1, 9)]
            + [{"date": f"2026-08-{day:02d}", "location": "Quan 1"} for day in range(1, 4)]
        )
        self.weather = {"temp_c": policy.HOT_SESSION_TEMP_C + 3.0, "feels_like_c": 30.6,
                        "humidity_pct": policy.HUMID_SESSION_PCT + 14, "station": "Seletar Airport"}

    def test_home_is_the_location_the_device_logged_most(self):
        env = analytics.build_environment_signal(self.location_days, self.weather, 5, {})

        self.assertEqual(env["home_location"], "Singapore")
        self.assertEqual(env["away_sessions"], 3)
        self.assertEqual(env["away_stays"][0]["location"], "Quan 1")
        self.assertEqual(env["away_stays"][0]["first"], "2026-08-01")
        self.assertEqual(env["away_stays"][0]["last"], "2026-08-03")

    def test_a_hot_humid_session_is_recognised_from_the_measurement(self):
        env = analytics.build_environment_signal(self.location_days, self.weather, 5, {})

        self.assertTrue(env["hot_session"])
        self.assertTrue(env["humid_session"])

        mild = analytics.build_environment_signal(
            self.location_days, {**self.weather, "temp_c": policy.HOT_SESSION_TEMP_C - 3.0}, 5, {}
        )
        self.assertFalse(mild["hot_session"])

    def test_heat_band_and_wording_come_from_policy(self):
        for pct, key in ((5, "none"), (25, "partial"), (60, "full")):
            with self.subTest(pct=pct):
                env = analytics.build_environment_signal(self.location_days, self.weather, pct, {})
                self.assertEqual(env["heat_band"], key)
                self.assertEqual(env["heat_tone"], policy.HEAT_BANDS[key]["tone"])
                self.assertEqual(env["heat_plain"], policy.HEAT_BANDS[key]["plain"])

    def test_missing_weather_publishes_nothing_invented(self):
        env = analytics.build_environment_signal(self.location_days, {}, None, {})

        self.assertEqual(env["weather"], {})
        self.assertFalse(env["hot_session"])
        self.assertIsNone(env["heat_band"])

    def test_hydration_reports_the_target_and_the_logged_intake(self):
        env = analytics.build_environment_signal(
            self.location_days, self.weather, 5, {"goal_ml": 2915.0, "intake_ml": 0.0, "sweat_loss_ml": 786.0}
        )

        self.assertEqual(env["hydration"]["goal_ml"], 2915.0)
        self.assertEqual(env["hydration"]["intake_pct"], 0)
        self.assertEqual(env["hydration"]["sweat_loss_ml"], 786.0)


class CapacitySignalTests(unittest.TestCase):
    """Capacity comes from the account's own profile, never from a constant."""

    def test_bmi_is_computed_from_the_profiles_own_height_and_weight(self):
        cap = analytics.build_capacity_signal(make_profile(), {}, {}, {}, "2026-09-20")

        self.assertEqual(cap["bmi"], round(63.5 / (1.67 ** 2), 1))
        self.assertEqual(cap["bmi_band"], policy.bmi_band(cap["bmi"]))
        self.assertEqual(cap["bmi_tone"], policy.BMI_BANDS[cap["bmi_band"]]["tone"])

    def test_no_profile_publishes_no_bmi_rather_than_a_typical_one(self):
        cap = analytics.build_capacity_signal({}, {}, {}, {}, "2026-09-20")

        self.assertIsNone(cap["bmi"])
        self.assertIsNone(cap["bmi_band"])
        self.assertIsNone(cap["vo2max"])

    def test_race_forecasts_are_formatted_from_the_devices_own_seconds(self):
        cap = analytics.build_capacity_signal(
            make_profile(),
            {"time5k": 1553, "time10k": 3295, "time_half": 7390, "time_marathon": 16197},
            {},
            {},
            "2026-09-20",
        )

        formatted = {race["label"]: race["formatted"] for race in cap["race_predictions"]}
        self.assertEqual(formatted["5K"], "25:53")
        self.assertEqual(formatted["Marathon"], "4h 29m")

    def test_intensity_band_and_plain_english_come_from_policy(self):
        cap = analytics.build_capacity_signal(
            make_profile(), {}, {"weekly_total": 20, "moderate": 20, "vigorous": 0, "goal": 150}, {}, "2026-09-20"
        )

        self.assertEqual(cap["intensity"]["band"], "low")
        self.assertEqual(cap["intensity"]["tone"], policy.INTENSITY_BANDS["low"]["tone"])
        self.assertEqual(cap["intensity"]["plain"], policy.INTENSITY_BANDS["low"]["plain"])


class FitnessFallbackTests(unittest.TestCase):
    """A failed endpoint publishes nothing, instead of a stranger's physiology."""

    def test_missing_fitness_age_is_none_not_a_placeholder(self):
        dq = DataQuality()
        result = source.fetch_fitness_and_workload(FakeClient(), "2026-09-20", make_profile(), dq)

        self.assertIsNone(result["fitness_age"])
        self.assertIsNone(result["achievable_fitness_age"])
        self.assertIsNone(result["acwr"])
        self.assertIsNone(result["acute_load"])
        self.assertNotIn("bmi", result)  # body composition is the profile's job now
        self.assertEqual(dq.metrics["fitness_age"]["source"], "fallback")

    def test_age_and_device_come_from_the_account(self):
        result = source.fetch_fitness_and_workload(FakeClient(), "2026-09-20", make_profile(), DataQuality())

        self.assertEqual(result["chronological_age"], 29)
        self.assertEqual(result["device_name"], "fenix 6S ASIA Sapphire")


class TodaySummaryTests(unittest.TestCase):
    """The top strip reports the published window, and invents nothing.

    It answers three questions -- today's steps, what was trained in the last 24
    hours, and last night -- so it has to be exact about the window it covers and
    quiet about anything the payload did not measure.
    """

    PUBLISHED = "2026-09-22T00:55:04+00:00"  # 08:55 SGT

    def _summary(self, **overrides):
        activities = overrides.pop("activities", [
            {"startTimeLocal": "2026-09-22 07:10:00", "category": "Walking", "duration_min": 20.0},
            {"startTimeLocal": "2026-09-22 00:30:00", "category": "Gym", "duration_min": 45.0},
            # Outside the 24-hour window: 08:55 SGT on the 20th is 48 hours back.
            {"startTimeLocal": "2026-09-20 08:00:00", "category": "Running", "duration_min": 30.0},
        ])
        today = overrides.pop("today", {
            "date": "2026-09-22", "sleep_time_seconds": 24300, "sleep_score": 80,
        })
        capacity = overrides.pop("capacity", {"day": {"steps": 8702, "step_goal": 10000}})
        step_days = overrides.pop("step_days", [
            {"calendarDate": "2026-09-21", "totalSteps": 9100, "stepGoal": 10000},
            {"calendarDate": "2026-09-22", "totalSteps": 45, "stepGoal": 10000},
        ])
        whoop = overrides.pop("whoop", {"baseline_sleep_need_hours": 7.5})
        published = overrides.pop("published_at", self.PUBLISHED)
        return analytics.build_today_summary(today, activities, capacity, step_days, whoop, published)

    def test_the_window_is_the_24_hours_ending_at_publication(self):
        movement = self._summary()["movement"]

        self.assertEqual(movement["sessions"], 2)  # the 20th is outside it
        self.assertEqual(movement["minutes"], 65)
        self.assertEqual(movement["categories"], ["Walking", "Gym"])
        self.assertEqual(movement["latest"]["category"], "Walking")
        self.assertEqual(movement["latest"]["hours_ago"], 2)

    def test_the_window_is_labelled_in_singapore_time(self):
        self.assertEqual(self._summary()["window_label"], "22 Sep, 08:55 SGT")

    def test_a_few_steps_early_in_the_day_is_not_reported_as_zero_percent(self):
        summary = self._summary(capacity={"day": {"steps": 45, "step_goal": 10000}})["steps"]

        self.assertEqual(summary["pct"], 0)
        self.assertEqual(summary["pct_display"], "under 1% of goal")

    def test_one_session_reads_as_one_session(self):
        summary = self._summary(activities=[
            {"startTimeLocal": "2026-09-22 07:10:00", "category": "Walking", "duration_min": 20.0},
        ])

        self.assertEqual(summary["movement"]["sessions"], 1)
        self.assertEqual(summary["movement"]["sessions_display"], "1 session")

    def test_steps_are_reported_against_the_goal_and_yesterdays_own_total(self):
        steps = self._summary()["steps"]

        self.assertEqual((steps["count"], steps["goal"], steps["pct"]), (8702, 10000, 87))
        self.assertEqual(steps["pct_display"], "87% of goal")
        self.assertEqual((steps["yesterday_count"], steps["yesterday_goal"]), (9100, 10000))

    def test_last_night_is_measured_against_the_published_sleep_need(self):
        sleep = self._summary()["sleep"]

        self.assertEqual(sleep["formatted"], "6h 45m")
        self.assertEqual(sleep["score"], 80)
        self.assertEqual(sleep["baseline_hours"], 7.5)
        self.assertEqual(sleep["pct_of_baseline"], 91)

    def test_an_empty_payload_reports_nothing_rather_than_a_number(self):
        summary = analytics.build_today_summary({}, [], {}, [], {}, None)

        self.assertIsNone(summary["window_label"])
        self.assertEqual(
            summary["steps"],
            {"count": None, "goal": None, "pct": None, "pct_display": None,
             "yesterday_count": None, "yesterday_goal": None},
        )
        self.assertEqual(summary["movement"]["sessions_display"], "0 sessions")
        self.assertEqual(summary["movement"]["sessions"], 0)
        self.assertIsNone(summary["movement"]["minutes"])
        self.assertIsNone(summary["movement"]["latest"])
        self.assertIsNone(summary["sleep"]["formatted"])
        self.assertIsNone(summary["sleep"]["pct_of_baseline"])

    def test_the_page_renders_absent_values_as_absent(self):
        page = (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )
        body = page.split("function renderTodaySummary", 1)[1].split("\n    function ", 1)[0]

        for element_id in ("glanceWindow", "glanceSteps", "glanceSessions", "glanceSleep",
                           "glanceStepsPct", "glanceSleepNeed"):
            with self.subTest(element=element_id):
                self.assertIn(f'"{element_id}"', body)
                # Each display line reports a measurement or "--", never a number.
                window = body.split(f'"{element_id}"', 1)[1].split(";", 1)[0]
                self.assertNotRegex(window, r'\|\|\s*[0-9"]', window)
        self.assertIn("measured(", body)

    def test_every_strip_value_starts_as_absent(self):
        page = (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )
        strip = page.split("id=\"glanceWindow\"", 1)[1].split("<!-- SECTION 0", 1)[0]

        for element_id in ("glanceSteps", "glanceSessions", "glanceSleep"):
            with self.subTest(element=element_id):
                tag = strip.split(f'id="{element_id}"', 1)[1].split("</", 1)[0]
                self.assertTrue(tag.rstrip().endswith("--"), tag)


class FitnessAgeOwnerTests(unittest.TestCase):
    """The advantage badge reads the engine, and an unmeasured age reads absent."""

    def test_the_tier_follows_the_two_ages(self):
        elite = policy.fitness_age_advantage(20.0, 29.0)
        younger = policy.fitness_age_advantage(24.7, 29.0)
        same = policy.fitness_age_advantage(29.0, 29.0)
        older = policy.fitness_age_advantage(31.0, 29.0)

        self.assertEqual((elite["key"], elite["label"]), ("elite", "9.0 yrs younger"))
        self.assertEqual((younger["key"], younger["label"]), ("younger", "4.3 yrs younger"))
        self.assertEqual(same["label"], "matches your age")
        self.assertEqual(older["label"], "2.0 yrs older")
        for entry in (elite, younger, same, older):
            with self.subTest(key=entry["key"]):
                self.assertTrue(entry["tone"])

    def test_an_unmeasured_age_is_never_an_advantage(self):
        for fitness_age, chronological_age in ((None, 29.0), (24.7, None), (None, None), ("", 29.0)):
            with self.subTest(fitness_age=fitness_age, chronological_age=chronological_age):
                entry = policy.fitness_age_advantage(fitness_age, chronological_age)
                self.assertEqual(entry["key"], "unmeasured")
                self.assertEqual(entry["label"], "--")
                self.assertIsNone(entry["years"])
                self.assertEqual(entry["tone"], "slate")

    def test_the_payload_carries_the_resolved_badge(self):
        fields = sync.age_advantage_fields({"fitness_age": 24.7, "chronological_age": 29})

        self.assertEqual(fields["age_advantage_label"], "4.3 yrs younger")
        self.assertEqual(fields["age_advantage_tone"], "emerald")
        self.assertEqual(fields["age_advantage_years"], 4.3)

        absent = sync.age_advantage_fields({"fitness_age": None, "chronological_age": 29})
        self.assertEqual(absent["age_advantage_label"], "--")
        self.assertEqual(absent["age_advantage_tone"], "slate")

    def test_the_page_holds_no_fitness_age_claim_of_its_own(self):
        page = self._page()
        for fragment in ("-4.3 yrs", "24.7</span>", "\u22124.3 years advantage"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, page)
        self.assertIn("fitness.age_advantage_label", page)
        self.assertIn("fitness.age_advantage_tone", page)

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )


class MotionDirectionTests(unittest.TestCase):
    """A bar fills from its own edge, and never stays empty waiting for a frame.

    The bars once animated with a scale spring that started at 92% of their width,
    which reads as a wobble in place rather than a fill, and a primed bar whose
    reveal never came rendered at zero width -- a measurement shown as nothing.
    """

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    def test_a_bar_fills_from_zero_from_its_own_edge(self):
        page = self._page()
        for keyframes in ("bar-spring", "bar-spring-y"):
            with self.subTest(keyframes=keyframes):
                body = page.split(f"@keyframes {keyframes} {{", 1)[1].split("}", 1)[0]
                self.assertIn("scaleX(0)" if keyframes == "bar-spring" else "scaleY(0)", body)
        self.assertIn("transform-origin: left center", page)
        self.assertIn("transform-origin: bottom center", page)

    def test_the_five_pillar_bars_are_primed_by_class_and_have_a_failsafe(self):
        page = self._page()
        self.assertIn("pillar-fill", page)
        self.assertIn("each('.pillar-fill', (el, index) => primeGrow(el, 'width', 80 * index));", page)
        # A reveal that never arrives must still leave the bar at its measured width,
        # and at the width the render published most recently rather than the one the
        # animation that started first happened to capture.
        grow = page.split("function primeGrow", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("setTimeout", grow)
        self.assertIn("el.style[prop] = motionTargets.get(el) || target;", grow)

    def test_every_word_heavy_panel_staggers_its_own_children(self):
        page = self._page()
        # The five panels that are mostly readings and prose, plus the cards the
        # render pass rebuilds, each hand their children to the reveal sweep.
        for host in (
            'data-stagger="90"',   # the briefing card
            'data-stagger="70"',   # the glance strip and the two card grids
            'data-stagger="80"',   # the signal cards and the dossier blocks
            'data-stagger="30"',   # one activity row per step
            "data-stagger-flip",
        ):
            with self.subTest(host=host):
                self.assertIn(host, page)
        stagger = page.split("function staggerChildren", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("Array.from(host.children)", stagger)
        self.assertIn("motion-tilt", stagger)
        # A long list must not wait minutes for its last row.
        self.assertIn("Math.min(index, 8)", stagger)
        # A stagger host animates its children, so the outer pass must leave it alone
        # rather than fading the same block twice.
        outer = page.split("document.querySelectorAll('section, .glass-card')", 1)[1].split("// iOS-style press feedback", 1)[0]
        self.assertIn("if (el.hasAttribute('data-stagger')) return;", outer)
        # The data-stagger pass itself now lives in the one owner that both the first
        # paint and every later replay apply, because a render does rebuild those hosts.
        self.assertIn("each('[data-stagger]', host => {", page)

    def test_a_missing_frame_never_leaves_a_count_on_a_wrong_number(self):
        page = self._page()
        count = page.split("function primeCount", 1)[1].split("\n    function ", 1)[0]
        # The interpolation is a frame loop; if the frames stop, the published value
        # must be written back rather than an interpolated one left on screen.
        self.assertIn("window.setTimeout(() => {", count)
        self.assertIn("el.textContent === lastFrame)", count)
        self.assertIn("el.textContent = motionTargets.get(el) || target;", count)

    def test_a_missing_frame_never_leaves_a_reveal_invisible(self):
        page = self._page()
        sweep = page.split("function queueSweep", 1)[1].split("\n    window.addEventListener", 1)[0]
        # The latch that debounces the sweep must also be cleared by a timer, or a
        # throttled frame loop blocks every later reveal and the page shows nothing.
        self.assertIn("requestAnimationFrame(run)", sweep)
        self.assertIn("window.setTimeout(run, 250)", sweep)
        self.assertIn("vizSweepQueued = false;", sweep)

    def test_the_new_entrances_are_off_for_reduced_motion(self):
        page = self._page()
        reduce_block = page.split("@media (prefers-reduced-motion: reduce)", 1)[1].split("/* Clinical Report", 1)[0]
        self.assertIn("#lockScreen .glass-card { animation: none !important; }", reduce_block)
        self.assertIn(".motion-tilt { opacity: 1 !important; transform: none !important; }", reduce_block)
        # The lock screen cannot wait for the motion-live marker: it is on screen
        # before the dashboard exists, so its rise is plain CSS.
        self.assertIn("#lockScreen .glass-card {\n        animation: lock-rise", page)

    def test_a_signed_reading_counts_through_its_own_sign(self):
        page = self._page()
        count = page.split("function primeCount", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("const match = target.match(/^([+-]?)([0-9][0-9,]*(?:\\.[0-9]+)?)(.*)$/)", count)
        self.assertIn("return `${sign}${shown}${suffix}`;", count)
        # A comma-grouped reading keeps its grouping while it rolls.
        self.assertIn("toLocaleString('en-SG')", count)
        # The illness markers and the glance strip now roll too.
        for el_id in ("markerDeltaRhr", "markerDeltaHrv", "markerRespRate", "markerSleepStress", "glanceSteps"):
            with self.subTest(el_id=el_id):
                self.assertIn(f"'{el_id}'", page)

    def test_the_scatter_animation_starts_every_point_above_the_plot(self):
        page = self._page()
        start = page.split("function scatterDropStart", 1)[1].split("\n    function ", 1)[0]

        self.assertIn("chartArea", start)
        self.assertIn("return top - ", start)
        # The drop is pixel-space, which is why it reads the chart area and not the data.
        self.assertIn("from: (ctx) => scatterDropStart(", page)
        self.assertIn("easing: 'easeOutQuad'", page)


class ReRenderMotionTests(unittest.TestCase):
    """A panel a re-render rebuilds animates again, and nothing is left stranded.

    The first paint primed the document once, and every render after it replaced
    those primed elements with plain ones -- the activity rows, the coaching cards,
    the five pillar bars, the heat-map cells -- so the page went static exactly where
    it should have felt alive. Each rebuild now replays its own panel through the
    same primitives, and those primitives refuse to fight over one element or to read
    back their own in-flight state as if it were a measurement.
    """

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    @staticmethod
    def _fn(page, name):
        return page.split(f"function {name}(", 1)[1].split("\n    function ", 1)[0]

    def test_each_rebuilt_panel_replays_its_own_motion(self):
        page = self._page()
        for panel, replay in (
            ("renderCoaching", "replayMotion(document.getElementById('coachCards'));"),
            ("renderFitbitPillars", "replayMotion(container);"),
            ("renderConsistencyGrid", "replayMotion(grid);"),
            ("renderConsistencyGrid", "replayMotion(focusGrid);"),
            ("toggleConsistencyFocus", "replayMotion(target);"),
        ):
            with self.subTest(panel=panel, replay=replay):
                self.assertIn(replay, self._fn(page, panel))
        # The table has two branches -- rows, and the empty message -- and the filter
        # reaches it without going through a dashboard render, so both must replay.
        table = self._fn(page, "renderActivityTable")
        self.assertEqual(table.count("replayMotion(tbody);"), 2)

    def test_the_replay_happens_where_the_panel_was_rebuilt(self):
        page = self._page()
        for panel in ("renderCoaching", "renderFitbitPillars", "renderConsistencyGrid", "renderActivityTable"):
            with self.subTest(panel=panel):
                body = self._fn(page, panel)
                # Replaying is the render's own last word on the panel it just wrote.
                self.assertIn("innerHTML", body)
                self.assertLess(body.index("innerHTML"), body.index("replayMotion("))

    def test_one_owner_applies_every_primitive(self):
        page = self._page()
        body = page.split("function primeMotion(scope) {", 1)[1].split("\n    function forgetPlayed", 1)[0]
        rest = page.replace(body, "")
        for call in ("primeGrow(", "primeGauge(", "primeScale(", "animateHeatmap(", "primeCount(", "staggerChildren("):
            with self.subTest(call=call):
                self.assertGreaterEqual(body.count(call), 1)
                # Only the definition of each primitive is left outside the owner.
                self.assertEqual(rest.count(call), 1)
        # The first paint and every replay apply that same owner.
        self.assertIn("primeMotion(document);", self._fn(page, "playVisuals"))
        self.assertIn("primeMotion(scope);", self._fn(page, "replayMotion"))

    def test_a_scoped_pass_covers_the_host_it_was_handed(self):
        page = self._page()
        body = page.split("function primeMotion(scope) {", 1)[1].split("\n    function forgetPlayed", 1)[0]
        # The coaching cards and the table body hand over their own data-stagger host,
        # so a scoped query that only looked at descendants would never see it.
        self.assertIn("root.matches(selector)", body)
        self.assertIn("(root === document || root === el || root.contains(el))", body)
        # A grid behind the closed modal is skipped: its cells are copies nobody sees.
        self.assertIn("grid.closest('.hidden')", body)

    def test_forgetting_a_mark_can_never_hide_an_element(self):
        page = self._page()
        forget = self._fn(page, "forgetPlayed")
        self.assertIn("vizPlayed.delete(root);", forget)
        self.assertIn("root.querySelectorAll('*').forEach(el => vizPlayed.delete(el));", forget)
        # Marks only. Removing a class is how a replay would hide a reading, so this
        # must never touch one -- the primitives re-add their own.
        self.assertNotIn("classList", forget)
        self.assertNotIn("style.", forget)

    def test_a_second_prime_stands_down_instead_of_reading_its_own_state(self):
        page = self._page()
        grow = self._fn(page, "primeGrow")
        # The zero stamped on a bar while its fill is in flight is not a measurement:
        # reading it back as one is exactly how a bar would drain to nothing.
        self.assertIn("if (!target || target === '0%') return;", grow)
        gauge = self._fn(page, "primeGauge")
        self.assertIn("if (!empty || !target || target === empty) return;", gauge)
        # And an animation that started earlier finishes on the value the newest render
        # published, never on the one it captured.
        for primitive in ("primeGrow", "primeGauge", "primeCount"):
            with self.subTest(primitive=primitive):
                self.assertIn("motionTargets.set(el, target);", self._fn(page, primitive))
        self.assertIn("el.style[prop] = motionTargets.get(el) || target;", grow)

    def test_a_second_render_replays_the_readings_it_rewrote(self):
        page = self._page()
        visuals = self._fn(page, "playVisuals")
        self.assertIn("if (!motionPainted) {", visuals)
        self.assertIn("forgetPlayed(document);", visuals)
        self.assertIn("motionPainted = true;", visuals)
        # The structural entrances belong to the document, which a re-render never
        # replaces, so they run once while the readings are primed on every pass.
        self.assertLess(visuals.index("if (!motionPainted) {"), visuals.index("forgetPlayed(document);"))
        self.assertLess(visuals.index("forgetPlayed(document);"), visuals.index("primeMotion(document);"))

    def test_the_replay_reuses_the_existing_failsafes(self):
        page = self._page()
        # A replay is carried by the same three timers every primable already relied on.
        # None was replaced, and none was duplicated.
        self.assertEqual(page.count("window.setTimeout(run, 250)"), 1)
        self.assertEqual(page.count("4000 + delay"), 1)
        self.assertEqual(page.count("duration + 1500"), 1)

    def test_the_replay_does_not_invent_a_new_kind_of_motion(self):
        page = self._page()
        self.assertEqual(
            re.findall(r"@keyframes ([\w-]+)", page),
            ["gauge-spring", "chart-spring", "bar-spring", "bar-spring-y", "heat-cell-spring",
             "pulse-dot", "meridian-spin", "spin", "lock-rise"],
        )
        # Replay re-runs the primitives; it never adds an effect of its own.
        self.assertNotIn("replayMotion", self._fn(page, "primeGrow"))
        self.assertNotIn("replayMotion", self._fn(page, "primeCount"))

    def test_a_replay_restarts_the_same_animation_instead_of_adding_one(self):
        page = self._page()
        restart = self._fn(page, "replayAnimation")
        # Removing the class, forcing a reflow and adding it back: the same keyframes,
        # which is what a bar the render rewrites in place needs to play them again.
        self.assertIn("el.classList.remove(className);", restart)
        self.assertIn("void el.offsetWidth;", restart)
        self.assertIn("el.classList.add(className);", restart)
        for primitive in ("primeGrow", "primeGauge", "primeScale"):
            with self.subTest(primitive=primitive):
                self.assertIn("replayAnimation(", self._fn(page, primitive))
        # It only ever restarts animations that already exist in the stylesheet.
        for name in ("bar-spring", "bar-spring-y", "motion-gauge-bounce", "motion-chart-settle"):
            self.assertIn(name, page)

    def test_a_reveal_never_holds_a_chart_it_can_no_longer_use(self):
        page = self._page()
        body = page.split("each('canvas', canvas => {", 1)[1].split("      });", 1)[0]
        # The instance is read inside the reveal rather than captured when it was
        # primed: a render rebuilds the charts, and resetting a destroyed one threw
        # straight out of the sweep, leaving every primable behind it unrevealed.
        self.assertIn("const live = window.Chart && Chart.getChart ? Chart.getChart(canvas) : null;", body)
        self.assertIn("if (!live) return;", body)
        self.assertIn("live.reset();", body)
        self.assertNotIn("chart.reset();", body)

class ThemeDefaultTests(unittest.TestCase):
    """Night mode is the default; day mode is a choice the reader makes.

    The palette is authored on the dark surface -- the gauge glows, the glass
    cards and the instrument tones -- so a first visit opens in the theme the
    terminal was drawn for. A saved choice still wins, and the theme is resolved
    inline so neither theme flashes while the page loads.
    """

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    def test_a_first_visit_opens_in_night_mode(self):
        page = self._page()
        resolver = page.split("var saved = localStorage.getItem('meridian_theme')", 1)[1].split("</script>", 1)[0]

        self.assertIn("saved === 'light' || saved === 'dark' ? saved : 'dark'", resolver)
        # No preference lookup: the operating system must not pick the theme.
        self.assertNotIn("prefers-color-scheme", resolver)
        self.assertNotIn("matchMedia", resolver)

    def test_the_document_starts_dark(self):
        page = self._page()
        self.assertIn('<html lang="en" data-theme="dark" class="dark">', page)

    def test_the_reader_choice_is_remembered_and_reversible(self):
        page = self._page()
        toggle = page.split("function toggleTheme()", 1)[1].split("\n    function ", 1)[0]

        self.assertIn("localStorage.setItem(THEME_KEY, next)", toggle)
        self.assertIn("dataset.theme === 'light' ? 'dark' : 'light'", toggle)


class SyncRelayTests(unittest.TestCase):
    """A sync can be started without a token in the page, and never from one.

    The dashboard is public, so the token that starts a sync lives in a relay's own
    secret rather than in `index.html`. These guards keep the two routes distinct:
    the relay pins the workflow and refuses a burst, and the page falls back to the
    browser token only when no relay is configured.
    """

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    @staticmethod
    def _worker():
        return (Path(__file__).resolve().parent.parent / "relay" / "worker.js").read_text(
            encoding="utf-8"
        )

    def test_the_page_prefers_a_relay_and_says_so_in_one_place(self):
        page = self._page()
        self.assertIn('const SYNC_RELAY_URL = "";', page)
        mode = page.split("function syncTriggerMode()", 1)[1].split("\n    function ", 1)[0]
        # A relay wins over a stored token; no token and no relay means re-read only.
        self.assertIn('if (relayConfigured()) return "relay";', mode)
        self.assertIn('return githubToken() ? "token" : null;', mode)
        # The tooltip is the button's only account of itself, and it follows the mode.
        button = page.split("function updateRefreshButton()", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("const mode = syncTriggerMode();", button)
        self.assertIn('? "Re-read the published vault, and trigger a fresh sync (Shortcut: R)"', button)
        self.assertIn(': "Re-read the published vault (Shortcut: R)";', button)

    def test_the_relay_route_sends_no_authorization_header(self):
        page = self._page()
        dispatch = page.split("async function dispatchSyncWorkflow()", 1)[1].split("\n    // Poll", 1)[0]
        relay_branch = dispatch.split("if (relayConfigured())", 1)[1].split("const resp = await fetch(", 1)[0]
        self.assertIn("SYNC_RELAY_URL", relay_branch)
        self.assertNotIn("Authorization", relay_branch)
        self.assertNotIn("githubToken()", relay_branch)
        # A refused burst is reported as its own outcome, not as a generic failure.
        self.assertIn("resp.status === 429", relay_branch)

    def test_the_worker_pins_the_target_and_needs_a_secret(self):
        worker = self._worker()
        self.assertIn('const WORKFLOW_FILE = "daily_sync.yml";', worker)
        self.assertIn('const REF = "main";', worker)
        # The request body must not be able to redirect the dispatch.
        self.assertNotIn("request.json()", worker)
        self.assertIn("env.GITHUB_TOKEN", worker)
        self.assertIn("if (!env.GITHUB_TOKEN)", worker)
        # A public page must not be able to spend the owner's Actions minutes.
        self.assertIn("MIN_INTERVAL_SECONDS", worker)
        self.assertIn("429", worker)
        # The origin is the only caller allowed.
        self.assertIn("if (origin !== allowed)", worker)

    def test_the_worker_never_returns_the_token(self):
        worker = self._worker()
        # GitHub's own error body can carry token detail, so only a hint goes back.
        self.assertIn("return json({ error: hint, status: resp.status }, 502", worker)
        # The dispatch response is inspected by status alone; only the runs lookup,
        # which is a plain list, is parsed.
        dispatch = worker.split("/dispatches`, out", 1)[1] if "/dispatches`, out" in worker else worker.split("body: JSON.stringify({ ref: REF }),\n      }\n    );", 1)[1]
        self.assertNotIn("resp.json()", dispatch)
        self.assertNotIn("resp.text()", dispatch)

    def test_the_relay_does_not_ship_a_credential(self):
        # SecretGuardTests scans the tree; this is the local sanity check that the
        # relay's secret is read from the environment and nowhere else.
        worker = self._worker()
        self.assertIn("wrangler secret put GITHUB_TOKEN", (Path(__file__).resolve().parent.parent / "relay" / "README.md").read_text(encoding="utf-8"))
        self.assertNotRegex(worker, r"github_pat_[A-Za-z0-9_]{16,}")
        self.assertNotRegex(worker, r"ghp_[A-Za-z0-9]{16,}")


class TactileAudioTests(unittest.TestCase):
    """Every cue is named once, and every press that does something says so.

    The frequencies used to be chosen at each call site, which is how a dashboard
    ends up with one panel that chirps at 900 and another that chirps at 560 for the
    same kind of action. The vocabulary lives in `SOUND_CUES`; these guards keep it
    the only source of a cue name and keep the quiet interactions quiet.
    """

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    @staticmethod
    def _cue_table(page):
        body = page.split("const SOUND_CUES = {", 1)[1].split("\n    };", 1)[0]
        return body

    def test_every_cue_name_is_in_the_vocabulary(self):
        page = self._page()
        vocabulary = self._cue_table(page)
        # Both shapes the page uses: a plain cue, and the open/close ternary (whose
        # condition can mention something that is not a cue at all, like a theme name).
        names = set(re.findall(r"playCue\('([a-z]+)'\)", page))
        for chosen, otherwise in re.findall(r"playCue\([^()]*\?\s*'([a-z]+)'\s*:\s*'([a-z]+)'\)", page):
            names.update((chosen, otherwise))
        self.assertGreaterEqual(len(names), 8, sorted(names))
        for name in sorted(names):
            with self.subTest(cue=name):
                self.assertIn(f"{name}: [", vocabulary)

    def test_the_vocabulary_is_one_table_and_stays_quiet(self):
        page = self._page()
        self.assertIn("function playCue(name)", page)
        route = page.split("function playCue(name)", 1)[1].split("\n    }", 1)[0]
        # Every cue is built from the one chirp primitive, so a single volume policy
        # applies to all of them.
        self.assertIn("playMicroChirp(freq, duration, type, volume)", route)
        self.assertIn("if (!isAudioEnabled || !SOUND_CUES[name]) return;", route)
        # Feedback, not notification: the volume is the fourth field of a cue's note,
        # and nothing may exceed 0.025. (The second field is a duration, which is
        # why this reads the position rather than every decimal in the table.)
        for note in re.findall(r"\[([^\]]+)\]", self._cue_table(page)):
            fields = [f.strip() for f in note.split(",")]
            if len(fields) < 4:
                continue
            with self.subTest(note=note):
                self.assertLessEqual(float(fields[3]), 0.025)

    def test_a_refused_press_and_a_failed_refresh_speak(self):
        page = self._page()
        guard_section = page.split("async function refreshVault()", 1)[1].split("const previousAt", 1)[0]
        # Three ways a press is declined, each answered aloud rather than with a toast
        # over silence.
        self.assertEqual(guard_section.count("playCue('refuse')"), 3)
        started = page.split("refreshBusy = true;", 1)[1].split("const previousAt", 1)[0]
        self.assertIn("playCue('start')", started)
        failure = page.split("const passphraseMissing", 1)[1].split("finally", 1)[0]
        self.assertIn("playCue('warn')", failure)
        # A finished job keeps the melody; nothing new is a step, not a fanfare.
        self.assertIn("if (changed) playSuccessMelody();", page)
        self.assertIn("else playCue('step');", page)

    def test_the_continuous_interactions_keep_their_own_pitch_ladders(self):
        page = self._page()
        # A scrub, a heat-map sweep and a section jump are continuous: they read as a
        # ladder of pitches, so they must not be reduced to one discrete cue per frame.
        self.assertIn("playMicroChirp(720 + (idx % 12) * 28, 0.018)", page)
        self.assertIn("playMicroChirp(600 + sectionIndex * 50, 0.03)", page)
        self.assertIn("lastChirpTime", page)

    def test_the_consequential_controls_are_audible(self):
        page = self._page()
        # Locking the vault, arming the emergency lockout and enlarging the heat map
        # were all silent; each now has a cue of its own.
        self.assertIn("function logout()", page)
        logout = page.split("function logout()", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("playCue('lock')", logout)
        self.assertIn("playCue('warn')", page.split("function openMasterLockModal()", 1)[1].split("\n    }", 1)[0])
        focus = page.split("function toggleConsistencyFocus", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("playCue(open ? 'enlarge' : 'close')", focus)


class SoundDefaultTests(unittest.TestCase):
    """The dashboard is audible on a first visit, and a refusal is remembered.

    The chirps are how a press reports back -- a refresh that landed, a filter that
    switched -- and the page is read before the reader is properly awake. So the
    default is on, and only an explicit "false" silences it; the toggle still writes
    the choice either way.
    """

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    def test_a_first_visit_is_audible(self):
        page = self._page()
        self.assertIn(
            'let isAudioEnabled = localStorage.getItem("garmin_sound_enabled") !== "false";',
            page,
        )
        self.assertNotIn('garmin_sound_enabled") === "true"', page)

    def test_turning_it_off_is_remembered(self):
        page = self._page()
        toggle = page.split("function toggleAudioSound()", 1)[1].split("\n    function ", 1)[0]
        self.assertIn('localStorage.setItem("garmin_sound_enabled", isAudioEnabled ? "true" : "false")', toggle)
        self.assertIn("updateAudioUi()", toggle)

    def test_the_first_sound_follows_a_gesture(self):
        page = self._page()
        # Browsers do not start audio without a gesture, so the context is resumed on
        # demand rather than assumed to be running.
        init = page.split("function initAudioContext()", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("audioCtx.state === 'suspended'", init)
        self.assertIn("audioCtx.resume()", init)


class NoInventedMeasurementTests(unittest.TestCase):
    """Absent stays absent on the dashboard, exactly as policy already renders it.

    The KPI strip used to fall back to plausible numbers -- a 0.2 ratio beside a
    dial that said it had nothing to point at, and an Acute Load of 44 that no
    device ever reported -- so a reader saw two answers for one missing reading.
    """

    CARD_IDS = (
        "kpiFitnessAge", "kpiChronologicalAge", "kpiAchievableAge", "kpiAcwrVal",
        "kpiAcuteLoad", "kpiChronicLoad", "kpiHrvValue", "kpiRhrValue", "cardRhrVal",
        "doughnutScoreVal", "fitbitReadinessScore", "metricEfficiency", "metric7dDebt",
        "markerRespRate", "markerSleepStress",
    )

    @staticmethod
    def _page():
        return (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8", errors="ignore"
        )

    def test_no_card_substitutes_a_plausible_number_for_a_missing_one(self):
        page = self._page()
        for element_id in self.CARD_IDS:
            with self.subTest(element=element_id):
                window = page.split(f'("{element_id}")', 1)[1].split(";", 1)[0]
                self.assertNotRegex(window, r'\|\|\s*[0-9"]', window)

    def test_each_card_starts_as_absent(self):
        page = self._page()
        for element_id in ("kpiFitnessAge", "kpiChronologicalAge", "kpiAcwrVal", "kpiAcuteLoad",
                           "kpiChronicLoad", "kpiFitnessAgeDelta", "kpiHrvValue", "kpiRhrValue"):
            with self.subTest(element=element_id):
                tag = page.split(f'id="{element_id}"', 1)[1].split("</", 1)[0]
                self.assertTrue(tag.rstrip().endswith("--"), tag)

    def test_the_absence_helper_is_what_renders_them(self):
        page = self._page()

        self.assertIn("function measured(", page)
        self.assertIn('measured(today.hrv_last_night)', page)
        self.assertIn('measured(fitness.acwr)', page)


class UnmeasuredWorkloadTests(unittest.TestCase):
    """An endpoint that answers with nothing must not take the whole run down.

    The runner hit exactly this: `get_training_status` returned nothing, every load
    field stayed None, and the readiness pillar compared that None against a number.
    The sync died before it could publish anything, so the athlete stopped receiving
    fresh biometrics at all -- a fallback is not a verdict, but a crash is no data.
    """

    # What the source publishes when the training-status endpoint answers with nothing.
    NO_LOAD = {"acute_load": None, "chronic_load": None, "acwr": None, "acwr_status": None}

    def test_readiness_survives_a_missing_training_load(self):
        fitbit = analytics.calculate_fitbit_metrics(
            make_today(), make_baselines(), make_fitness(**self.NO_LOAD)
        )

        self.assertIsInstance(fitbit["daily_readiness_score"], int)
        self.assertIsInstance(fitbit["fatigue_component_score"], int)
        for pillar in fitbit["health_metrics_5_pillars"]:
            for field in ("value", "baseline", "range_min", "range_max"):
                with self.subTest(pillar=pillar["key"], field=field):
                    self.assertIsInstance(pillar[field], (int, float))

    def test_a_snapshot_of_nones_cannot_kill_the_run(self):
        today = dict.fromkeys(make_today())
        today["date"] = "2026-09-20"
        baselines = dict.fromkeys(make_baselines())

        fitbit = analytics.calculate_fitbit_metrics(today, baselines, make_fitness(**self.NO_LOAD))

        self.assertIsInstance(fitbit["daily_readiness_score"], int)

    def test_the_narrative_claims_no_band_it_could_not_measure(self):
        verdict = clinical_engine.deterministic_engine(
            make_today(), make_baselines(), make_fitness(**self.NO_LOAD)
        )

        paragraph = verdict["workload_and_biological_age"]
        self.assertNotIn("None", paragraph)
        self.assertNotIn(policy.ACWR_BANDS["under"]["plain"], paragraph)
        self.assertIn("no load band", paragraph)
        self.assertIn("no load band", verdict["plain_english"]["workload_and_biological_age"])

    def test_a_measured_load_still_names_its_band(self):
        verdict = clinical_engine.deterministic_engine(make_today(), make_baselines(), make_fitness())

        paragraph = verdict["workload_and_biological_age"]
        self.assertIn(policy.ACWR_BANDS["under"]["label"].lower(), paragraph)
        self.assertIn("Acute Load of 40", paragraph)
        self.assertIn(
            policy.ACWR_BANDS["under"]["plain"], verdict["plain_english"]["workload_and_biological_age"]
        )


# ---------------------------------------------------------------------------
# Correlation lab
# ---------------------------------------------------------------------------

class CorrelationTests(unittest.TestCase):
    """A published correlation must carry its coefficient, its day count and its meaning."""

    @staticmethod
    def _series(key_a, key_b, days=40, slope=1.0, key_c=None):
        dates = [f"2026-08-{day:02d}" for day in range(1, days + 1)]
        series = {key_a: {d: 50 + i for i, d in enumerate(dates)}}
        series[key_b] = {d: 55 + i * slope for i, d in enumerate(dates)}
        if key_c:
            series[key_c] = {d: 90 + (i % 3) for i, d in enumerate(dates)}
        return series

    def test_a_strong_pair_is_published_with_its_day_count_and_note(self):
        result = bio_correlate.build_correlations(self._series("hrv", "sleep_score"))

        finding = result["findings"][0]
        self.assertEqual(finding["r"], 1.0)
        self.assertEqual(finding["days"], 40)
        self.assertEqual(finding["strength"], "strong")
        self.assertEqual(finding["direction"], "higher")
        self.assertEqual(finding["note"], policy.PAIR_NOTES[("hrv", "sleep_score")])

    def test_a_thin_series_is_refused_rather_than_published(self):
        result = bio_correlate.build_correlations(self._series("hrv", "sleep_score", days=policy.CORRELATION_MIN_DAYS - 1))

        self.assertEqual(result["findings"], [])
        self.assertEqual(result["skipped"][0]["days"], policy.CORRELATION_MIN_DAYS - 1)

    def test_a_weak_coefficient_is_not_a_finding(self):
        series = {
            "hrv": {f"2026-08-{d:02d}": 50 + (d % 2) for d in range(1, 41)},
            "sleep_score": {f"2026-08-{d:02d}": 70 + (d % 7) for d in range(1, 41)},
        }
        result = bio_correlate.build_correlations(series)

        for finding in result["findings"]:
            self.assertGreaterEqual(abs(finding["r"]), policy.CORRELATION_MIN_R)

    def test_only_curated_pairs_are_tested(self):
        # A pair with a strong relationship but no physiological reason behind it
        # must not be published, and must not even be computed.
        series = self._series("hrv", "sleep_score")
        series["steps"] = {d: 9000 + i * 100 for i, d in enumerate(sorted(series["hrv"]))}
        result = bio_correlate.build_correlations(series)

        pairs = {tuple(sorted((f["metric_a"], f["metric_b"]))) for f in result["findings"]}
        self.assertTrue(pairs.issubset({tuple(sorted(p)) for p in policy.PAIR_NOTES}))
        self.assertNotIn(("hrv", "sleep_score"), set())  # sanity: the real pair survived
        self.assertIn(("hrv", "sleep_score"), pairs)

    def test_a_channel_that_never_moved_cannot_correlate(self):
        flat = {
            "hrv": {f"2026-08-{d:02d}": 50 for d in range(1, 41)},
            "sleep_score": {f"2026-08-{d:02d}": 70 + d for d in range(1, 41)},
        }
        r, n = bio_correlate.pearson(flat["hrv"], flat["sleep_score"])

        self.assertIsNone(r)
        self.assertEqual(n, 40)

    def test_readings_that_were_not_paired_do_not_count_as_days(self):
        series = self._series("hrv", "sleep_score")
        for date in list(series["sleep_score"])[:30]:
            series["sleep_score"].pop(date)

        r, n = bio_correlate.pearson(series["hrv"], series["sleep_score"])

        self.assertIsNone(r)  # 10 paired days is below the minimum
        self.assertEqual(n, 10)

    def test_strength_wording_tracks_the_coefficient(self):
        self.assertEqual(bio_correlate.strength_word(0.8), "strong")
        self.assertEqual(bio_correlate.strength_word(-0.6), "clear")
        self.assertEqual(bio_correlate.strength_word(0.36), "modest")


class LocationComparisonTests(unittest.TestCase):
    """Home/away differences are only published with enough days on both sides."""

    @staticmethod
    def _inputs(home_days, away_days):
        locations = (
            [{"date": f"2026-09-{d:02d}", "location": "Singapore"} for d in range(1, home_days + 1)]
            + [{"date": f"2026-08-{d:02d}", "location": "Quan 1"} for d in range(1, away_days + 1)]
        )
        metrics = {
            "hrv": {f"2026-09-{d:02d}": 54.0 for d in range(1, home_days + 1)}
                   | {f"2026-08-{d:02d}": 50.0 for d in range(1, away_days + 1)},
        }
        return locations, metrics

    def test_enough_nights_both_sides_publishes_the_difference(self):
        locations, metrics = self._inputs(10, 6)
        result = bio_correlate.location_breakdown(locations, metrics)

        self.assertEqual(result["home_location"], "Singapore")
        self.assertEqual(result["away_location"], "Quan 1")
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["home_mean"], 54.0)
        self.assertEqual(comparison["away_mean"], 50.0)
        self.assertEqual(comparison["delta"], -4.0)

    def test_too_few_away_nights_publishes_nothing(self):
        locations, metrics = self._inputs(20, policy.TRAVEL_MIN_NIGHTS - 1)

        self.assertIsNone(bio_correlate.location_breakdown(locations, metrics))

    def test_no_travel_at_all_publishes_nothing(self):
        locations, metrics = self._inputs(20, 0)

        self.assertIsNone(bio_correlate.location_breakdown(locations, metrics))

    def test_sessions_without_a_logged_location_are_not_counted_as_travel(self):
        locations, metrics = self._inputs(10, 6)
        # Twelve unlocated sessions used to outnumber the real trip and become the
        # "away" group, producing a travel comparison that never happened.
        locations += [{"date": f"2026-07-{day:02d}", "location": None} for day in range(1, 13)]
        metrics["hrv"].update({f"2026-07-{day:02d}": 40.0 for day in range(1, 13)})

        result = bio_correlate.location_breakdown(locations, metrics)

        self.assertEqual(result["away_location"], "Quan 1")
        self.assertEqual(result["away_days"], 6)
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["away_mean"], 50.0)
        self.assertEqual(comparison["away_days"], 6)

    def test_every_away_place_feeds_the_average_not_just_the_busiest(self):
        locations = (
            [{"date": f"2026-09-{d:02d}", "location": "Singapore"} for d in range(1, 11)]
            + [{"date": f"2026-08-{d:02d}", "location": "Quan 1"} for d in range(1, 5)]
            + [{"date": f"2026-07-{d:02d}", "location": "Air Hitam"} for d in range(1, 5)]
        )
        metrics = {
            "hrv": {f"2026-09-{d:02d}": 54.0 for d in range(1, 11)}
                   | {f"2026-08-{d:02d}": 50.0 for d in range(1, 5)}
                   | {f"2026-07-{d:02d}": 60.0 for d in range(1, 5)},
        }

        result = bio_correlate.location_breakdown(locations, metrics)

        self.assertEqual(result["away_location"], "Quan 1")
        self.assertEqual(result["away_days"], 8)
        self.assertEqual(result["away_location_count"], 2)
        self.assertEqual(result["comparisons"][0]["away_mean"], 55.0)  # both places, not just Quan 1


class ChannelSeriesTests(unittest.TestCase):
    """The correlation window is built from measured days only."""

    def test_each_channel_carries_only_the_days_it_was_measured(self):
        sleep_history = [
            {"date": "2026-09-19", "score": 80, "total_seconds": 27000, "deep_seconds": 6000,
             "rem_seconds": 4000, "avg_stress": 18, "avg_respiration": 13.0},
            {"date": "2026-09-20", "score": None, "total_seconds": 0, "avg_stress": None},
        ]
        series = analytics.daily_channel_series(
            sleep_history,
            [{"calendarDate": "2026-09-20", "lastNightAvg": 52}],
            [{"calendarDate": "2026-09-19", "value": 49}],
            [{"calendarDate": "2026-09-20", "totalSteps": 8700}],
            [{"date": "2026-09-20", "latest": 94}],
        )

        self.assertEqual(series["hrv"], {"2026-09-20": 52})
        self.assertEqual(series["rhr"], {"2026-09-19": 49})
        self.assertEqual(series["steps"], {"2026-09-20": 8700})
        self.assertEqual(series["spo2"], {"2026-09-20": 94})
        # A night with no score contributes no sleep-score day at all.
        self.assertEqual(series["sleep_score"], {"2026-09-19": 80})
        self.assertNotIn("2026-09-20", series["sleep_hours"])

    def test_a_channel_with_no_days_is_omitted_entirely(self):
        series = analytics.daily_channel_series([], [], [{"calendarDate": "2026-09-20", "value": 49}], [], [])

        self.assertEqual(list(series), ["rhr"])


# ---------------------------------------------------------------------------
# The movement card reads a week and a month, with the heart beside it
# ---------------------------------------------------------------------------

class MovementTrendTests(unittest.TestCase):
    """Every figure is a measured average, or absent. No day is invented as zero."""

    def _channels(self, steps=None, rhr=None, hrv=None):
        def dated(values):
            return {f"2026-08-{day:02d}": value for day, value in (values or {}).items()}

        return {"steps": dated(steps), "rhr": dated(rhr), "hrv": dated(hrv)}

    def test_the_week_and_month_averages_come_from_the_measured_days_only(self):
        steps = {day: 9000 for day in range(1, 32)}
        steps[25] = 1000  # a quiet day inside the week
        trend = analytics.build_movement_trend(self._channels(steps=steps), "2026-08-31", 9000)

        # The week is the seven finished days ending 30 Aug, so days 24-30.
        self.assertEqual(trend["window"]["label"], "24-30 Aug")
        self.assertEqual(trend["week"]["days_measured"], 7)
        self.assertEqual(trend["week"]["mean_steps"], int(round((9000 * 6 + 1000) / 7)))
        self.assertEqual(trend["week"]["prior_mean_steps"], 9000)
        self.assertEqual(trend["week"]["days_met_goal"], 6)  # every measured day but the quiet one
        self.assertEqual(trend["week"]["pct_of_goal"], int(round(trend["week"]["mean_steps"] / 9000 * 100)))
        self.assertEqual(trend["month"]["days_measured"], 30)

    def test_a_gap_in_the_record_shrinks_the_week_rather_than_counting_zero(self):
        steps = {day: 9000 for day in range(1, 31) if day not in (28, 29)}
        trend = analytics.build_movement_trend(self._channels(steps=steps), "2026-08-31", 10000)

        self.assertEqual(trend["week"]["days_measured"], 5)
        self.assertEqual(trend["week"]["mean_steps"], 9000)

    def test_a_month_that_repeats_the_week_is_not_printed_twice(self):
        trend = analytics.build_movement_trend(
            self._channels(steps={day: 8000 for day in range(24, 31)}), "2026-08-31", 10000
        )

        self.assertEqual(trend["plain"].count("8,000 steps"), 1)

    def test_the_heart_split_uses_the_readers_own_median_and_both_sides(self):
        steps = {day: 5000 + day * 400 for day in range(1, 30)}
        rhr = {day: 60 - day // 4 for day in range(1, 30)}
        hrv = {day: 40 + day // 3 for day in range(1, 30)}
        trend = analytics.build_movement_trend(
            self._channels(steps=steps, rhr=rhr, hrv=hrv), "2026-08-31", 20000
        )

        heart = trend["heart"]
        self.assertIsNotNone(heart)
        self.assertGreater(heart["rhr_low"], heart["rhr_high"])  # busier days sat lower
        self.assertLess(heart["hrv_low"], heart["hrv_high"])
        self.assertEqual(heart["high_days"] + heart["low_days"], 29)
        # The comparison is published, not narrated: the card prints it once, beside the
        # ring, so `plain` carries no second copy of the same figures.
        self.assertNotIn("resting heart rate", trend["plain"])
        self.assertNotIn(str(heart["median_steps"]), trend["plain"])

    def test_a_short_history_publishes_no_comparison_rather_than_a_thin_one(self):
        trend = analytics.build_movement_trend(
            self._channels(steps={day: 8000 for day in range(1, 9)},
                           rhr={day: 50 for day in range(1, 9)}), "2026-08-09", 10000
        )

        self.assertIsNone(trend["heart"])
        self.assertNotIn("resting heart rate", trend["plain"])

    def test_no_steps_at_all_reads_as_absent_everywhere(self):
        trend = analytics.build_movement_trend({}, "2026-08-31", 10000)

        self.assertFalse(trend["available"])
        for key in ("week", "month", "heart", "plain"):
            self.assertIsNone(trend[key])

    def test_a_single_measured_day_reads_as_one_day(self):
        trend = analytics.build_movement_trend(
            self._channels(steps={30: 12000}), "2026-08-31", 10000
        )

        self.assertNotIn("1 measured days", trend["plain"])
        self.assertIn("The last finished day (30 Aug)", trend["plain"])
        self.assertIn("which reached your 10,000-step goal", trend["plain"])

    def test_the_day_still_running_is_left_out_of_the_average(self):
        """45 steps at 09:38 must not drag the week average down all morning."""
        steps = {day: 10000 for day in range(15, 22)}  # 15-21 Aug, finished days
        steps[22] = 45  # today, still accumulating
        trend = analytics.build_movement_trend(self._channels(steps=steps), "2026-08-22", 10000)

        self.assertEqual(trend["window"]["end"], "2026-08-21")
        self.assertEqual(trend["week"]["days_measured"], 7)
        self.assertEqual(trend["week"]["mean_steps"], 10000)
        self.assertEqual(trend["week"]["days_met_goal"], 7)

    def test_a_day_that_is_only_running_publishes_nothing(self):
        trend = analytics.build_movement_trend(
            self._channels(steps={22: 45}), "2026-08-22", 10000
        )

        self.assertFalse(trend["available"])
        self.assertIsNone(trend["plain"])


# ---------------------------------------------------------------------------
# The profile is the only source of personal facts
# ---------------------------------------------------------------------------

class ProfileHonestyTests(unittest.TestCase):
    """The prompt may only describe the athlete with measured facts."""

    def _prompt(self, context=None):
        return clinical_engine._build_prompt(
            {"hrv_last_night": 52, "rhr": 49, "sleep_stress": 18, "sleep_time_seconds": 24500,
             "deep_sleep_seconds": 5400, "rem_sleep_seconds": 3300, "sleep_score": 76,
             "body_battery_charged": 47, "respiration_rate": 13.0, "lowest_respiration": 9.0,
             "hrv_status": "BALANCED"},
            {"hrv_30d": 55.1, "hrv_normal_range": [54, 72], "hrv_180d": 58.8, "rhr_30d": 49.8,
             "rhr_180d": 49.5, "rhr_all_time": 50.0, "deep_sleep_pct_180d": 23.3,
             "rem_sleep_pct_30d": 15.0, "respiration_avg_30d": 13.0},
            {"chronological_age": 29, "fitness_age": 24.7, "device_name": "fenix 6S ASIA Sapphire",
             "acute_load": 43, "chronic_load": 219, "acwr": 0.1, "achievable_fitness_age": 21.1},
            context,
        )

    def test_the_prompt_states_profile_facts_rather_than_assuming_them(self):
        prompt = self._prompt()

        self.assertIn("Age 29", prompt)
        self.assertIn("fenix 6S ASIA Sapphire", prompt)
        self.assertIn("no occupation, employer, diet", prompt)

    def test_no_invented_biography_reaches_the_model(self):
        prompt = self._prompt()

        for invented in ("software", "farm executive", "Oura", "Whoop/"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, prompt)

    def test_measured_context_is_offered_when_it_exists(self):
        prompt = self._prompt({
            "profile": make_profile(),
            "oxygen": {"available": True, "latest": 94, "latest_date": "2026-09-20",
                       "sleep_average": 93.0, "lowest": 84, "days_recorded": 13, "window_days": 120},
            "environment": {"home_location": "Singapore", "away_stays": [{"location": "Quan 1", "sessions": 10}],
                            "weather": {"temp_c": 27.2, "humidity_pct": 89}, "heat_acclimation_pct": 5},
            "capacity": {"bmi": 22.8, "intensity": {"weekly_total": 20, "goal": 150}},
        })

        self.assertIn("Blood oxygen: latest 94%", prompt)
        self.assertIn("recorded on 13 of the last 120 days", prompt)
        self.assertIn("Singapore", prompt)
        self.assertIn("heat acclimation 5%", prompt)
        self.assertIn("20 of 150 weekly intensity minutes", prompt)

    def test_missing_context_says_not_recorded_rather_than_guessing(self):
        prompt = self._prompt()

        self.assertIn("Blood oxygen: not recorded in the last 120 days", prompt)
        self.assertIn("Training logged around: not recorded", prompt)

    def test_rule_engine_uses_measured_conditions_not_a_named_city(self):
        verdict = clinical_engine.deterministic_engine(
            {"hrv_last_night": 52, "rhr": 49, "sleep_stress": 18, "sleep_time_seconds": 24500,
             "deep_sleep_seconds": 5400, "rem_sleep_seconds": 3300},
            {"hrv_30d": 55.1, "rhr_30d": 49.8, "deep_sleep_pct_180d": 23.3},
            {"chronological_age": 29, "fitness_age": 24.7, "acwr": 0.1},
            {"environment": {"home_location": "Singapore", "weather": {"temp_c": 27.2, "humidity_pct": 89},
                             "heat_acclimation_pct": 5,
                             "hydration": {"goal_ml": 2915.0, "sweat_loss_ml": 786.0}}},
        )

        self.assertIn("training is logged around Singapore", verdict["workload_and_biological_age"])
        self.assertIn("latest session ran at 27.2C", verdict["workload_and_biological_age"])
        self.assertTrue(any("Hydration & Heat" in d for d in verdict["actionable_directives"]))
        self.assertFalse(any("3.0L" in d for d in verdict["actionable_directives"]))
        self.assertTrue(all("Singapore" not in d or "logged" in d for d in verdict["actionable_directives"]))


class SecretGuardTests(unittest.TestCase):
    """A credential must never reach this repository.

    The dashboard is published as a static site, so anything committed here is
    public the moment it is pushed -- and secret scanning would revoke a leaked
    token only after it had already been readable. The refresh flow therefore
    keeps the viewer's token in their own browser storage, and this guard exists
    so nobody can quietly put one in a file instead, including by pasting one
    into a doc that then gets committed.
    """

    CREDENTIAL_PATTERNS = (
        re.compile(r"ghp_[A-Za-z0-9]{16,}"),          # classic personal access token
        re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),  # fine-grained token
        re.compile(r"gho_[A-Za-z0-9]{16,}"),          # OAuth token
        re.compile(r"ghs_[A-Za-z0-9]{16,}"),          # app installation token
        re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),       # Google API key
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )

    # Everything a committed file could plausibly be: sources, docs, the page,
    # the workflows and the tests. The encrypted vault under data/ is produced by
    # the pipeline and is checked by its own decryption test instead.
    SCANNED_SUFFIXES = (".py", ".md", ".html", ".json", ".yml", ".yaml", ".mjs", ".js", ".txt")

    def _tracked_files(self):
        root = Path(__file__).resolve().parent.parent
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in self.SCANNED_SUFFIXES:
                continue
            if {".git", "__pycache__", "cache", "spo2", "sleep", "node_modules"} & set(path.parts):
                continue
            if path.name == "status.json":
                continue
            yield path

    def test_no_credential_pattern_appears_in_any_source_file(self):
        offenders = []
        for path in self._tracked_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern in self.CREDENTIAL_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{path.name}: {pattern.pattern}")

        self.assertEqual(offenders, [], f"credential-like string committed: {offenders}")

    def test_the_token_flow_stays_in_browser_storage(self):
        page = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8", errors="ignore")
        # The only place the token may live is localStorage. It is written by the
        # one-time link, not by a field in the page.
        self.assertIn("localStorage.setItem(GITHUB_TOKEN_KEY, supplied)", page)
        self.assertIn("const GITHUB_TOKEN_KEY = \"garmin_github_token\";", page)
        self.assertNotIn("GITHUB_TOKEN =\u0009", page)

    def test_there_is_no_token_field_in_the_page(self):
        page = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8", errors="ignore")
        # A settings dialog is a place a credential can be typed, screenshotted and
        # pasted; the header carries none, and the onboarding path is a link whose
        # fragment never reaches a server.
        for gone in ("refreshSettingsModal", "ghTokenInput", "saveRefreshSettings", "clearGitHubToken", "refreshSettingsBtn"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, page)
        self.assertIn("function applyTokenFromFragment()", page)
        self.assertIn("history.replaceState(null, \"\", location.pathname + location.search)", page)
        self.assertIn("#token=", page)
        # Pasting the link onto an open dashboard changes only the fragment, so the
        # reader must not have to reload the page for it to take effect.
        self.assertIn("window.addEventListener('hashchange', applyTokenFromFragment)", page)
        # An empty value forgets; a value that is not a token is refused rather than
        # stored as if it were one.
        self.assertIn("looksLikeToken", page)
        self.assertIn("localStorage.removeItem(GITHUB_TOKEN_KEY)", page)


class CoachCardTests(unittest.TestCase):
    """Coaching is built from measured sessions, and says so when it cannot be."""

    TODAY = "2026-09-21"

    @staticmethod
    def _session(day, kind="strength_training", minutes=40.0, heart=120, gain=0.0, km=0.0):
        return {
            "startTimeLocal": f"{day} 08:00:00",
            "activityType": kind,
            "category": "Gym" if "strength" in kind else "Running",
            "duration_min": minutes,
            "averageHR": heart,
            "elevationGain": gain,
            "distance_km": km,
        }

    def _coach(self, activities=(), steps=(), sleep=(), fitness=None, readiness=None, environment=None, hrv=()):
        return bio_coach.build_coaching(
            self.TODAY,
            list(activities),
            list(steps),
            list(sleep),
            {"accumulated_7d_debt_hours": 2.0, "recommended_bedtime_minutes": 1380},
            {"deep_sleep_pct": 23.0},
            fitness or {"acwr": 1.0, "acwr_band": "sweet", "acwr_band_label": "Sweet Spot"},
            readiness or {"score": 80, "band": "PRIME", "tone": "green", "hrv_baseline": 55.0},
            environment or {"heat_acclimation_pct": 5, "heat_label": "Not Acclimated"},
            {"hrv_last_night": 54},
            list(hrv),
        )

    def _card(self, result, key):
        return next(card for card in result["cards"] if card["key"] == key)

    def test_every_card_carries_a_visual_of_its_own_reading(self):
        """A card with measurement draws it; a card without one draws nothing.

        The visual is built by the same module that writes the sentence, and it
        publishes the share of the scale each bar is drawn at, so the page never
        decides how a reading should look.
        """
        result = self._coach(
            activities=[self._session("2026-09-15"), self._session("2026-09-18")],
            steps=[{"calendarDate": f"2026-09-{d:02d}", "totalSteps": 9000} for d in range(15, 22)],
            sleep=[make_sleep(f"2026-09-{d:02d}", bedtime=1380) for d in range(10, 22)],
        )

        measured = [card for card in result["cards"] if card["measured"]]
        self.assertTrue(measured)
        for card in measured:
            with self.subTest(card=card["key"]):
                visual = card["visual"]
                self.assertIsNotNone(visual, card["key"])
                if visual["kind"] == "level":
                    self.assertTrue(0 <= visual["pct"] <= 100)
                else:
                    self.assertEqual(len(visual["bars"]), 7)
                    for bar in visual["bars"]:
                        if bar["value"] is not None:
                            self.assertTrue(0 <= bar["pct"] <= 100)
                        else:
                            self.assertIsNone(bar["pct"])

        # A domain with no history publishes no chart, rather than a chart of zeroes.
        empty = self._coach()
        for card in empty["cards"]:
            if not card["measured"]:
                self.assertIsNone(card["visual"], card["key"])

    def test_a_missing_step_day_is_a_gap_and_a_sessionless_day_is_a_zero(self):
        """The two absences mean different things and are published differently."""
        result = self._coach(
            activities=[self._session("2026-09-18")],
            steps=[{"calendarDate": f"2026-09-{d:02d}", "totalSteps": 9000} for d in (15, 16, 17)],
        )
        walking = self._card(result, "walking")
        strength = self._card(result, "strength")
        dates = {bar["date"]: bar["value"] for bar in walking["visual"]["bars"]}
        minutes = {bar["date"]: bar["value"] for bar in strength["visual"]["bars"]}

        self.assertIsNone(dates["2026-09-18"])  # the device recorded no step total
        self.assertEqual(minutes["2026-09-17"], 0.0)  # the day passed with no session
        self.assertEqual(minutes["2026-09-18"], 40.0)

    def test_the_coach_and_the_movement_card_quote_one_week_average(self):
        """Both read the finished days, so they cannot disagree about the same steps.

        The coach card and `bio_analytics.build_movement_trend` used to compute a
        "week average" over slightly different windows, which is how one reading ends
        up carrying two numbers on one page.
        """
        days = {15: 8384, 16: 4745, 17: 717, 18: 7804, 19: 116, 20: 8765, 21: 45}
        steps = [{"calendarDate": f"2026-09-{day:02d}", "totalSteps": value} for day, value in days.items()]
        walking = self._card(self._coach(steps=steps), "walking")
        trend = analytics.build_movement_trend(
            {"steps": {f"2026-09-{day:02d}": value for day, value in days.items()}},
            self.TODAY,
            10000,
        )

        bars = walking["visual"]["bars"]
        measured = [bar["value"] for bar in bars if bar["value"] is not None]

        # Same seven finished days, today's still-running count left out of both, and
        # the same average to the digit.
        self.assertEqual([bar["date"] for bar in bars], [f"2026-09-{day:02d}" for day in range(14, 21)])
        self.assertEqual(round(sum(measured) / len(measured)), trend["week"]["mean_steps"])
        self.assertIn(f"{trend['week']['mean_steps']:,} steps a day", walking["verdict"])

    def test_every_domain_publishes_a_card_with_all_five_parts(self):
        result = self._coach(
            activities=[self._session("2026-09-15"), self._session("2026-09-18")],
            steps=[{"calendarDate": f"2026-09-{d:02d}", "totalSteps": 9000} for d in range(15, 22)],
            sleep=[make_sleep(f"2026-09-{d:02d}", bedtime=1380) for d in range(10, 22)],
        )

        published = {card["key"] for card in result["cards"]}
        self.assertEqual(published, {key for key, _, _ in policy.COACH_DOMAINS})
        for card in result["cards"]:
            self.assertTrue(card["verdict"], card["key"])
            self.assertTrue(card["action"], card["key"])
            self.assertTrue(card["progression"], card["key"])
            self.assertTrue(card["guardrail"], card["key"])
            self.assertTrue(card["evidence"], card["key"])

    def test_a_domain_with_no_measured_sessions_prescribes_nothing_generic(self):
        result = self._coach()
        card = self._card(result, "endurance")

        self.assertFalse(card["measured"])
        self.assertEqual(card["tone"], "slate")
        self.assertEqual(card["metrics"], [])
        self.assertIn("Log one run", card["action"])
        self.assertIn("no running", card["basis"])

    def test_a_missing_step_series_reads_as_absent_rather_than_zero(self):
        result = self._coach()
        card = self._card(result, "walking")

        self.assertFalse(card["measured"])
        self.assertEqual(card["action"], "--")
        self.assertIn("no step totals", card["basis"])

    def test_strength_gap_is_quoted_against_the_measured_rate(self):
        # Two sessions inside a 28-day window is half a session a week, and one
        # extra session takes it to 0.8 -- the arithmetic the athlete can check.
        result = self._coach(activities=[self._session("2026-09-15"), self._session("2026-09-18")])
        card = self._card(result, "strength")

        self.assertEqual(card["tone"], "amber")
        self.assertIn("0.5 strength sessions a week", card["verdict"])
        self.assertIn("0.8 a week", card["action"])

    def test_a_met_strength_target_is_told_to_hold(self):
        # Eight sessions inside a 28-day window is the two a week the policy asks
        # for, so the card stops asking and starts protecting.
        result = self._coach(
            activities=[
                self._session(f"2026-09-{day:02d}") for day in (1, 3, 6, 8, 11, 13, 16, 18)
            ]
        )
        card = self._card(result, "strength")

        self.assertEqual(card["tone"], "green")
        self.assertTrue(card["action"].startswith("Hold"))

    def test_a_dangerous_ratio_hands_the_day_to_recovery(self):
        result = self._coach(
            fitness={"acwr": 1.7, "acwr_band": "danger", "acwr_band_label": "Danger Zone"},
            readiness={"score": 40, "band": "RED", "tone": "rose", "hrv_baseline": 55.0},
        )

        self.assertEqual(result["focus"]["key"], "recovery")
        self.assertEqual(self._card(result, "recovery")["tone"], "rose")
        self.assertIn("easy or off", self._card(result, "recovery")["action"])

    def test_an_under_loaded_week_is_given_room_and_a_high_one_is_capped(self):
        under = self._coach(fitness={"acwr": 0.5, "acwr_band": "under", "acwr_band_label": "Fresh / Under-trained"},
                            activities=[self._session("2026-09-15", kind="running", km=5.0)])
        high = self._coach(fitness={"acwr": 1.4, "acwr_band": "high", "acwr_band_label": "High"},
                           activities=[self._session("2026-09-15", kind="running", km=5.0)])

        self.assertIn("Add one easy 30-minute session", self._card(under, "endurance")["action"])
        self.assertIn("Cap the volume", self._card(high, "endurance")["action"])

    def test_walking_below_target_names_the_weakest_day(self):
        low = {"2026-09-20": 2000, "2026-09-19": 3000, "2026-09-18": 4000}
        steps = [
            {"calendarDate": f"2026-09-{d:02d}", "totalSteps": low.get(f"2026-09-{d:02d}", 9000)}
            for d in range(15, 22)
        ]
        result = self._coach(steps=steps)
        card = self._card(result, "walking")

        self.assertEqual(card["tone"], "amber")
        self.assertIn("2026-09-20", card["action"])
        self.assertIn("2,000", card["action"])

    def test_walking_on_target_is_told_the_extra_return_is_small(self):
        steps = [{"calendarDate": f"2026-09-{d:02d}", "totalSteps": 9500} for d in range(15, 22)]
        card = self._card(self._coach(steps=steps), "walking")

        self.assertEqual(card["tone"], "green")
        self.assertIn("Hold it", card["action"])

    def test_hiking_is_graded_by_climb_per_kilometre(self):
        steep = self._coach(activities=[self._session("2026-09-14", kind="hiking", minutes=90, gain=500, km=6.0)])
        flat = self._coach(activities=[self._session("2026-09-14", kind="hiking", minutes=90, gain=40, km=6.0)])

        self.assertIn("steep ground", self._card(steep, "hiking")["action"])
        self.assertIn("mostly flat walking", self._card(flat, "hiking")["action"])
        self.assertEqual(self._card(steep, "hiking")["tone"], "green")
        self.assertEqual(self._card(flat, "hiking")["tone"], "amber")

    def test_sleep_prescribes_extension_only_above_the_debt_threshold(self):
        nights = [make_sleep(f"2026-09-{d:02d}", bedtime=1380) for d in range(10, 21)]
        carried = bio_coach.build_coaching(
            self.TODAY, [], [], nights,
            {"accumulated_7d_debt_hours": policy.SLEEP_DEBT_ACTION_HOURS + 0.9, "recommended_bedtime_minutes": 1350},
            {}, {"acwr": 1.0, "acwr_band": "sweet", "acwr_band_label": "Sweet Spot"},
            {"score": 80, "band": "PRIME", "tone": "green", "hrv_baseline": 55.0},
            {}, {"hrv_last_night": 54}, [],
        )

        self.assertIn("Repay 3.4 h of debt", self._card(carried, "sleep")["action"])
        self.assertIn("lights out by 22:30", self._card(carried, "sleep")["action"])

    def test_sleep_prescribes_an_anchor_when_the_bedtime_moves(self):
        # Bedtimes spread by hours, which is the regularity signal rather than the
        # duration one, so the card must ask for a window instead of more sleep.
        nights = [make_sleep(f"2026-09-{d:02d}", bedtime=1380 + (d % 2) * 180) for d in range(4, 21)]
        card = self._card(self._coach(sleep=nights), "sleep")

        self.assertIn("Anchor your bedtime inside", card["action"])
        self.assertIn("Bedtime spread", [metric["label"] for metric in card["metrics"]])


class CoachEvidenceTests(unittest.TestCase):
    """Every rule cites a study, and the citation admits what it does not settle."""

    def test_every_anchor_carries_a_source_a_finding_and_a_caveat(self):
        for key, anchor in policy.EVIDENCE.items():
            self.assertTrue(anchor["claim"], key)
            self.assertTrue(anchor["source"], key)
            self.assertTrue(anchor["finding"], key)
            self.assertTrue(anchor["caveat"], key)

    def test_an_unknown_anchor_id_resolves_to_nothing(self):
        self.assertEqual(policy.evidence("not_a_study", "load_ratio"), [dict(id="load_ratio", **policy.EVIDENCE["load_ratio"])])

    def test_every_published_card_cites_at_least_one_known_anchor(self):
        result = bio_coach.build_coaching(
            "2026-09-21", [], [], [], {}, {},
            {"acwr": 1.0, "acwr_band": "sweet", "acwr_band_label": "Sweet Spot"},
            {"score": 80, "band": "PRIME", "tone": "green", "hrv_baseline": 55.0},
            {}, {"hrv_last_night": 54}, [],
        )
        for card in result["cards"]:
            self.assertTrue(card["evidence"], card["key"])
            for item in card["evidence"]:
                self.assertIn(item["id"], policy.EVIDENCE)
                self.assertEqual(item["source"], policy.EVIDENCE[item["id"]]["source"])

    def test_every_domain_has_a_stated_tie_break_priority(self):
        self.assertEqual(
            set(policy.COACH_PRIORITY),
            {key for key, _, _ in policy.COACH_DOMAINS},
        )


class CoachPatternTests(unittest.TestCase):
    """A personal pattern needs enough paired days, and both sides compared."""

    TODAY = "2026-09-21"

    @staticmethod
    def _days(count, start=0):
        base = datetime(2026, 9, 21) - timedelta(days=start)
        return [(base - timedelta(days=offset)).date().isoformat() for offset in range(count)]

    def _pattern_inputs(self, hard_every=4, days=48, hrv_after_hard=46, hrv_after_rest=56):
        """Alternate hard and rest days, with HRV answering each kind.

        The hard-day set is built first and the HRV value is then decided by
        whether the *previous* day is in that set, so the two groups line up with
        what the pattern code will actually compute.
        """
        dates = list(reversed(self._days(days)))
        hard = {index for index in range(days) if index % hard_every == 0}
        activities = [
            {
                "startTimeLocal": f"{dates[index]} 07:00:00",
                "activityType": "running",
                "duration_min": 60.0,
                "averageHR": 150,
                "distance_km": 10.0,
            }
            for index in sorted(hard)
        ]
        hrv = [
            {
                "calendarDate": day,
                "lastNightAvg": hrv_after_hard if (index - 1) in hard else hrv_after_rest,
            }
            for index, day in enumerate(dates)
        ]
        return activities, hrv

    def _patterns(self, activities, hrv, days=48):
        return bio_coach.personal_patterns(
            activities, [], hrv, [], bio_coach._date_of(self.TODAY)
        )

    def test_a_pattern_is_published_once_both_sides_have_enough_days(self):
        activities, hrv = self._pattern_inputs()
        patterns = {pattern["key"]: pattern for pattern in self._patterns(activities, hrv)}

        self.assertIn("hrv_after_load", patterns)
        pattern = patterns["hrv_after_load"]
        self.assertIn("46.0 ms", pattern["finding"])
        self.assertIn("56.0 ms", pattern["finding"])
        self.assertEqual(pattern["kind"], "association")
        self.assertGreaterEqual(pattern["pairs"], policy.PATTERN_MIN_PAIRS)

    def test_a_thin_history_publishes_no_pattern_at_all(self):
        activities, hrv = self._pattern_inputs(days=9)
        patterns = {pattern["key"] for pattern in self._patterns(activities, hrv)}

        self.assertNotIn("hrv_after_load", patterns)

    def test_rest_days_are_counted_rather_than_skipped(self):
        # The comparison is only meaningful if the quiet side is in it: a version
        # that dropped "after a rest day" would report the hard days alone, and the
        # reported mean would be the hard-day mean rather than a contrast.
        activities, hrv = self._pattern_inputs(hard_every=6, days=60)
        pattern = next(
            item for item in self._patterns(activities, hrv) if item["key"] == "hrv_after_load"
        )

        after_hard = sum(1 for index in range(60) if (index - 1) % 6 == 0)
        self.assertEqual(pattern["pairs"], 60)
        self.assertGreater(after_hard, 6)
        self.assertLess(after_hard, pattern["pairs"])
        self.assertIn("56.0 ms", pattern["finding"])

    def test_aerobic_efficiency_compares_early_runs_with_recent_ones(self):
        dates = list(reversed(self._days(40)))
        activities = []
        for index, day in enumerate(dates[:18]):
            activities.append({
                "startTimeLocal": f"{day} 07:00:00",
                "activityType": "running",
                "duration_min": 40.0,
                "averageHR": 150,
                # Later runs cover more ground at the same heart rate.
                "distance_km": 5.0 + index * 0.12,
            })
        pattern = next(
            item for item in self._patterns(activities, [], days=40) if item["key"] == "aerobic_efficiency"
        )

        self.assertIn("metres per heartbeat", pattern["finding"])
        self.assertIn("+", pattern["finding"])
        self.assertEqual(pattern["tone"], "green")

    def test_the_caveat_says_an_association_is_not_a_cause(self):
        result = bio_coach.build_coaching(
            self.TODAY, [], [], [], {}, {},
            {"acwr": 1.0, "acwr_band": "sweet", "acwr_band_label": "Sweet Spot"},
            {"score": 80, "band": "PRIME", "tone": "green", "hrv_baseline": 55.0},
            {}, {"hrv_last_night": 54}, [],
        )

        self.assertIn("does not establish that one caused the other", result["pattern_caveat"])

    def test_the_caveat_has_one_owner_and_carries_no_editorial_aside(self):
        """The coach panel and the correlation lab publish the same sentence.

        They used to hold their own copies, one of them in the browser, so the
        two could drift. Both now read `policy.CORRELATION_CAVEAT`, and the
        wording stays a neutral statement of what a pattern can and cannot say.
        """
        coached = bio_coach.build_coaching(
            self.TODAY, [], [], [], {}, {},
            {"acwr": 1.0, "acwr_band": "sweet", "acwr_band_label": "Sweet Spot"},
            {"score": 80, "band": "PRIME", "tone": "green", "hrv_baseline": 55.0},
            {}, {"hrv_last_night": 54}, [],
        )
        correlations = bio_correlate.build_correlations({})

        self.assertEqual(coached["pattern_caveat"], policy.CORRELATION_CAVEAT)
        self.assertEqual(correlations["caveat"], policy.CORRELATION_CAVEAT)
        for aside in ("not why", "is a guess", "honestly"):
            self.assertNotIn(aside, policy.CORRELATION_CAVEAT)

        page = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn("corr.caveat", page)
        self.assertNotIn("not why", page)
        self.assertNotIn("is a guess", page)

    def test_classification_routes_climbing_to_strength_and_running_to_endurance(self):
        self.assertEqual(bio_coach._classify({"activityType": "bouldering"}), "strength")
        self.assertEqual(bio_coach._classify({"activityType": "hiking"}), "hiking")
        self.assertEqual(bio_coach._classify({"activityType": "walking"}), "walking")
        self.assertEqual(bio_coach._classify({"activityType": "treadmill_running"}), "endurance")
        self.assertEqual(bio_coach._classify({"activityType": "multi_sport"}), "other")


class SignalPayloadTests(unittest.TestCase):
    """The new groups must reach the payload with their provenance recorded."""

    def test_payload_ships_the_measured_signal_groups(self):
        client = FakeClient(
            stress={"stressValuesArray": [[i, 20] for i in range(60)]},
            body_battery=[{"charged": 41, "drained": 12}],
            summary={"totalSteps": 9100, "averageStressLevel": 21},
        )
        fetched = {
            "today_str": "2026-09-20",
            "profile": make_profile(),
            "rhr": make_rhr("2026-06-01", 120),
            "hrv": make_hrv("2026-06-01", 120),
            "sleep": [make_sleep(f"2026-0{month}-{day:02d}", bedtime=1380)
                      for month in (8, 9) for day in range(1, 11)],
            "fitness": make_fitness(),
            "activities": [],
            "location": {"location_days": [], "weather": {}, "weather_live": False},
            "spo2": [{"date": "2026-09-19", "latest": 95, "sleep_average": 94.0, "lowest": 90,
                      "latest_time_local": "2026-09-19T21:10:00.0"}],
            "steps": [{"calendarDate": f"2026-09-{day:02d}", "totalSteps": 8000} for day in range(1, 21)],
            "hydration": {},
            "daily_activity": {},
            "intensity": {},
            "races": {},
        }
        dq = DataQuality()
        for key in ("sleep", "rhr", "hrv"):
            dq.record(key, True, "fixture")

        payload = sync.build_payload(client, fetched, dq)

        self.assertEqual(payload["oxygen"]["latest"], 95)
        self.assertEqual(payload["capacity"]["bmi"], round(63.5 / (1.67 ** 2), 1))
        self.assertIn("findings", payload["correlations"])
        self.assertIn("home_location", payload["environment"])
        # Year-on-year RHR is still published, and no field invented a person.
        self.assertEqual(payload["athlete"]["chronological_age"], 29)
        self.assertNotIn("bmi", payload["fitness"])


if __name__ == "__main__":
    unittest.main()
