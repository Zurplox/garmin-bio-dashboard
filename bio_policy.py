"""
System policy: every threshold, band, tone and metric name in one place.

Both halves of the system read from here. Python imports the values directly,
and `policy_snapshot()` is embedded in the encrypted payload so the browser can
render the same bands instead of keeping a second copy of the numbers.

The split of responsibility this enables:

* this module decides *what a number means* (band, tone, label, advice);
* the analytics layer computes numbers;
* the dashboard renders bands, tones and labels it is handed, and never
  compares a metric against a threshold itself.

That is deliberate. The recovery zone used to be derived independently in the
pipeline and in the browser, and the two drifted far enough that a shipped
payload reported a score of 68 alongside a "YELLOW" label.
"""

# --- environment -----------------------------------------------------------

SGT_OFFSET_MINUTES = 8 * 60
TARGET_WAKE_MINUTES = 7 * 60  # 07:00 SGT

# --- model coefficients ----------------------------------------------------

STEPS_SATURATION = 12000.0  # step volume at which ambulatory load saturates
SLEEP_CYCLE_MINUTES = 90
TARGET_SLEEP_HOURS = 7.5
BASELINE_SLEEP_NEED_MINUTES = 450  # 7h30m
SLEEP_ONSET_ALLOWANCE_MINUTES = 20
SLEEP_DEBT_DIVISOR = 3
SLEEP_DEBT_FLOOR_MINUTES = 15
SLEEP_DEBT_CEILING_MINUTES = 55
STRAIN_TO_SLEEP_MINUTES = 3.2

# --- band boundaries -------------------------------------------------------

RECOVERY_GREEN_MIN = 67
RECOVERY_YELLOW_MIN = 34

READINESS_PRIME_MIN = 75
READINESS_READY_MIN = 50
READINESS_EASY_MIN = 25

INJURY_LOW_MAX = 25
INJURY_MODERATE_MAX = 55

STRESS_REST_MAX = 25
STRESS_LOW_MAX = 50
STRESS_MED_MAX = 75

BODY_BATTERY_FULL_MIN = 35
BODY_BATTERY_PARTIAL_MIN = 20

# Resting heart rate, measured against the population ladder the dashboard's RHR
# panel documents (elite 38-48, athletic 48-58, average 60-80).
RHR_ELITE_MAX = 48
RHR_ATHLETIC_MAX = 58
RHR_AVERAGE_MAX = 80

DAY_STRAIN_SCALE_MAX = 21.0
HRV_LOW_BASELINE_RATIO = 0.85
STALE_AFTER_HOURS = 30

# Illness screening thresholds used by the clinical engine.
ILLNESS_MODERATE_DELTA_RHR = 2.5
ILLNESS_HIGH_DELTA_RHR = 4.0
ILLNESS_MODERATE_DELTA_HRV_PCT = -10.0
ILLNESS_HIGH_DELTA_HRV_PCT = -15.0
ILLNESS_SLEEP_STRESS_MAX = 25

# --- metric vocabulary -----------------------------------------------------

METRIC_LABELS = {
    "sleep": "Sleep architecture",
    "rhr": "Resting heart rate",
    "hrv": "Overnight HRV",
    "fitness_age": "Biological fitness age",
    "training_load": "Workload balance (ACWR)",
    "activities": "Workout activity feed",
    "body_battery": "Body Battery",
    "user_summary": "Steps & daily stress average",
    "stress_distribution": "24-hour stress distribution",
    "circadian": "Circadian sleep-onset timing",
}

# Metrics the dashboard is meaningless without.
CORE_METRICS = ("sleep", "rhr", "hrv")

# --- bands -----------------------------------------------------------------
# A band is the meaning of a number; a tone is the colour that meaning always
# carries, so "green means good" holds on every surface.

# The complete colour vocabulary. The dashboard maps tones to classes and
# strokes, and nothing else in either half may invent a colour.
TONE_NAMES = ("green", "amber", "rose", "cyan")

RECOVERY_ZONE_LABELS = {
    "green": "GREEN (OPTIMAL RECOVERY)",
    "yellow": "YELLOW (ADEQUATE RECOVERY)",
    "red": "RED (HIGH NEUROLOGICAL STRAIN)",
}
RECOVERY_TONES = {"green": "green", "yellow": "amber", "red": "rose"}

# Sentence-case form used inside generated prose ("today landed in the ... zone").
RECOVERY_BRIEFING_LABELS = {
    "green": "Green (optimal)",
    "yellow": "Yellow (moderate)",
    "red": "Red (poor)",
}

STRAIN_TARGETS = {
    "green": {
        "min": 13.0,
        "max": 17.5,
        "zone": "Optimal Overreach (Green Recovery)",
        "advice": "Green recovery confirms high nervous system capacity. Push intensity with heavy intervals or strength work.",
    },
    "yellow": {
        "min": 9.0,
        "max": 13.0,
        "zone": "Maintenance (Yellow Recovery)",
        "advice": "Moderate recovery capacity. Maintain cardiovascular tone without reaching acute fatigue.",
    },
    "red": {
        "min": 4.0,
        "max": 8.5,
        "zone": "Restorative (Red Recovery)",
        "advice": "Autonomic strain detected. Restrict exertion to active recovery, walking, and mobility.",
    },
}

READINESS_BANDS = {
    "prime": {
        "badge": "PRIME",
        "tone": "green",
        "text": "Your body is primed. This is a great day for high-intensity training, PR attempts, or competitions.",
        "briefing": "prime for peak training",
    },
    "ready": {
        "badge": "READY",
        "tone": "amber",
        "text": "Good recovery. You can handle moderate-to-hard training today. Prioritise quality over volume.",
        "briefing": "ready for moderate training",
    },
    "easy": {
        "badge": "EASY DAY",
        "tone": "amber",
        "text": "Partial recovery. Keep intensity low today. Walking, yoga, or easy movement only.",
        "briefing": "suitable for easy movement only",
    },
    "rest": {
        "badge": "REST",
        "tone": "rose",
        "text": "Your body needs rest. Training today will delay recovery and increase injury risk.",
        "briefing": "in need of full rest",
    },
}

INJURY_BANDS = {
    "low": {"badge": "LOW", "tone": "green"},
    "moderate": {"badge": "MODERATE", "tone": "amber"},
    "high": {"badge": "HIGH", "tone": "rose"},
}

STRESS_BANDS = {
    "rest": {"label": "REST", "tone": "green"},
    "low": {"label": "LOW", "tone": "cyan"},
    "med": {"label": "MED", "tone": "amber"},
    "high": {"label": "HIGH", "tone": "rose"},
}

BODY_BATTERY_BANDS = {
    "full": {"badge": "FULLY RECHARGED", "tone": "green"},
    "partial": {"badge": "PARTIAL RECHARGE", "tone": "amber"},
    "low": {"badge": "LOW RECHARGE", "tone": "rose"},
}

# Resting heart rate tiers. The badge beside the number and the panel's
# reference bands are the same ladder, so they cannot teach different things.
RHR_TIERS = {
    "elite": {"badge": "ELITE", "tone": "cyan"},
    "athletic": {"badge": "ATHLETIC", "tone": "green"},
    "average": {"badge": "AVERAGE", "tone": "amber"},
    "elevated": {"badge": "ELEVATED", "tone": "rose"},
}

# Overnight HRV relative to the athlete's own 30-day baseline.
HRV_BANDS = {
    "above": {"tone": "green"},
    "near": {"tone": "amber"},
    "below": {"tone": "rose"},
}

# Illness radar risk levels map onto the same tone vocabulary.
RISK_TONES = {"LOW": "green", "MODERATE": "amber", "HIGH": "rose"}


# --- resolvers -------------------------------------------------------------

def recovery_band(score):
    if score >= RECOVERY_GREEN_MIN:
        return "green"
    if score >= RECOVERY_YELLOW_MIN:
        return "yellow"
    return "red"


def recovery_zone(score):
    """Payload-facing recovery zone label, always consistent with the score."""
    return RECOVERY_ZONE_LABELS[recovery_band(score)]


def strain_target(recovery_score):
    return STRAIN_TARGETS[recovery_band(recovery_score)]


def readiness_band(score):
    if score >= READINESS_PRIME_MIN:
        return "prime"
    if score >= READINESS_READY_MIN:
        return "ready"
    if score >= READINESS_EASY_MIN:
        return "easy"
    return "rest"


def injury_band(risk_pct):
    if risk_pct < INJURY_LOW_MAX:
        return "low"
    if risk_pct < INJURY_MODERATE_MAX:
        return "moderate"
    return "high"


def stress_band(average):
    if average <= STRESS_REST_MAX:
        return "rest"
    if average <= STRESS_LOW_MAX:
        return "low"
    if average <= STRESS_MED_MAX:
        return "med"
    return "high"


def body_battery_band(charged):
    if charged >= BODY_BATTERY_FULL_MIN:
        return "full"
    if charged >= BODY_BATTERY_PARTIAL_MIN:
        return "partial"
    return "low"


def rhr_tier(value):
    """Population tier for a resting heart rate.

    The boundaries are inclusive at the top of each band so they line up with
    the ladder the RHR info panel shows; "athletic" therefore runs 49-58 bpm and
    nothing on the page has to re-derive the cut-offs.
    """
    if value <= RHR_ELITE_MAX:
        return "elite"
    if value <= RHR_ATHLETIC_MAX:
        return "athletic"
    if value <= RHR_AVERAGE_MAX:
        return "average"
    return "elevated"


def hrv_band(hrv, baseline):
    if not baseline:
        return "above"
    if hrv >= baseline:
        return "above"
    if hrv >= baseline * HRV_LOW_BASELINE_RATIO:
        return "near"
    return "below"


def acwr_workload_band(acwr):
    """The five-step training-load ladder, shared by every surface."""
    if acwr < 0.5:
        return "Very Light"
    if acwr < 0.8:
        return "Light"
    if acwr < 1.3:
        return "Moderate"
    if acwr < 1.5:
        return "Heavy"
    return "Too Heavy"


def policy_snapshot():
    """The policy numbers the dashboard genuinely needs at render time.

    Deliberately tiny: every band, badge and tone the UI displays arrives in the
    payload already resolved, so the browser has no thresholds to compare
    against and cannot drift from the engine.
    """
    return {
        "day_strain": {"scale_max": DAY_STRAIN_SCALE_MAX},
        "freshness": {"stale_after_hours": STALE_AFTER_HOURS},
    }
