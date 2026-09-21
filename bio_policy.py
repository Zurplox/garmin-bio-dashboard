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
    "spo2": "Blood oxygen (Pulse Ox)",
    "profile": "Athlete profile (age, body, VO2max)",
    "environment": "Location, climate & heat adaptation",
    "hydration": "Hydration balance",
    "intensity_minutes": "Weekly intensity minutes",
    "race_predictions": "Race time predictions",
    "daily_activity": "Daily activity totals",
    "steps_history": "Daily step history",
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

# The ACWR bands, named with the guide's own words so the badge, the readiness
# card and the ACWR panel cannot describe the same ratio differently. Garmin's own
# status word is context for that panel, never a second set of names.
#
# Each band also carries the plain-English restatement of what it means for today,
# which is what closes the workload analysis paragraph (see `clinical_engine`).
# Keeping it on the band rather than in a second table means one band can never
# have two explanations to disagree with each other.
ACWR_SWEET_MIN = 0.8
ACWR_SWEET_MAX = 1.3
ACWR_SAFE_MAX = 1.5

ACWR_BANDS = {
    "under": {
        "label": "Fresh / Under-trained",
        "tone": "cyan",
        "plain": "you have room to add training, and little injury risk in doing so",
    },
    "sweet": {
        "label": "Sweet Spot",
        "tone": "green",
        "plain": "this is the range where fitness improves fastest with the least injury risk",
    },
    "high": {
        "label": "High",
        "tone": "amber",
        "plain": "your load has risen faster than your body has adapted, so hold volume steady for a few days",
    },
    "danger": {
        "label": "Danger Zone",
        "tone": "rose",
        "plain": "this is the load pattern that usually comes before an injury, so cut volume now",
    },
}

# Overnight HRV relative to the athlete's own 30-day baseline. This band is the
# only meaning HRV has: the KPI badge, the Autonomic State row, the pillar dot and
# the quadrant matrix all read it, so no surface can call the same reading
# healthy and strained at once. Garmin's own status word is context for the HRV
# panel, never a second verdict that could outrank the band.
HRV_BANDS = {
    "above": {
        "tone": "green",
        "label": "Resilient (Balanced)",
        "plain": "you have recovered, and a hard session is well tolerated today",
    },
    "near": {
        "tone": "amber",
        "label": "Below Baseline",
        "plain": "this is an ordinary day-to-day dip -- train as planned, but do not chase a personal best",
    },
    "below": {
        "tone": "rose",
        "label": "Suppressed",
        "plain": "your body has not finished recovering -- keep today light and go to bed earlier",
    },
}

# Illness radar risk levels map onto the same tone vocabulary.
RISK_TONES = {"LOW": "green", "MODERATE": "amber", "HIGH": "rose"}


# --- blood oxygen ----------------------------------------------------------
# Pulse Ox on this watch records opportunistically: a spot reading most days and a
# sleep summary on some nights, not a nightly average. Coverage is therefore a
# published fact beside the value, because a number with no coverage line reads
# like a nightly average it never was.
SPO2_LOOKBACK_DAYS = 120
SPO2_MAX_NEW_FETCHES_PER_RUN = 25  # keeps a cold cache from hammering the API
SPO2_NORMAL_MIN = 95
SPO2_MILD_MIN = 92
SPO2_DIP_MIN = 90  # a single reading at or below this is worth flagging

SPO2_BANDS = {
    "normal": {
        "label": "In Range",
        "tone": "green",
        "plain": "your blood oxygen stayed in the normal range while you slept",
    },
    "mild": {
        "label": "Slightly Low",
        "tone": "amber",
        "plain": "your blood oxygen sat a little below the normal range -- worth watching if it repeats",
    },
    "low": {
        "label": "Low",
        "tone": "rose",
        "plain": "your blood oxygen sat below the normal range, which can follow illness, altitude or a loose sensor fit",
    },
}

# --- environment -----------------------------------------------------------
HEAT_ACCLIMATION_PARTIAL_MIN = 20
HEAT_ACCLIMATION_FULL_MIN = 50

HEAT_BANDS = {
    "none": {
        "label": "Not Acclimated",
        "tone": "cyan",
        "plain": "your body has not yet adapted to training in the heat",
    },
    "partial": {
        "label": "Partly Acclimated",
        "tone": "amber",
        "plain": "you are part-way through adapting to heat, so expect a slightly higher heart rate outdoors",
    },
    "full": {
        "label": "Heat Acclimated",
        "tone": "green",
        "plain": "your body is adapted to training in the heat, which lowers the heart-rate cost of a hot session",
    },
}

# A session counts as heat exposure at or above these, measured by the device's
# own weather station rather than assumed from where the athlete lives.
HOT_SESSION_TEMP_C = 28.0
HUMID_SESSION_PCT = 75.0

# Nights needed on one side of a home/away split before its average is published.
TRAVEL_MIN_NIGHTS = 5
TRAVEL_WINDOW_DAYS = 120

# Which metrics the home/away comparison reports, in this order and no further.
# Nine metrics in one sentence is a wall of numbers nobody reads; these four are
# the ones a trip plausibly moves.
TRAVEL_METRIC_ORDER = ("hrv", "rhr", "sleep_score", "sleep_hours")
TRAVEL_MAX_COMPARISONS = 4

# --- training capacity -----------------------------------------------------
INTENSITY_GOAL_MINUTES = 150  # WHO-equivalent moderate-intensity weekly target
INTENSITY_MET_MIN = 150
INTENSITY_PARTIAL_MIN = 75

INTENSITY_BANDS = {
    "met": {
        "label": "Target Met",
        "tone": "green",
        "plain": "you have already hit the weekly guideline for moderate-intensity movement",
    },
    "partial": {
        "label": "Partly There",
        "tone": "amber",
        "plain": "you are part of the way to the weekly moderate-intensity guideline",
    },
    "low": {
        "label": "Below Target",
        "tone": "cyan",
        "plain": "most of this week has been easy movement rather than moderate-intensity minutes",
    },
}

# BMI is computed from the athlete's own profile (height and weight), never assumed.
BMI_NORMAL_MIN = 18.5
BMI_OVERWEIGHT_MIN = 25.0
BMI_OBESE_MIN = 30.0

BMI_BANDS = {
    "under": {"label": "Under Range", "tone": "cyan"},
    "normal": {"label": "In Range", "tone": "green"},
    "overweight": {"label": "Above Range", "tone": "amber"},
    "obese": {"label": "High", "tone": "rose"},
}

# --- correlations ----------------------------------------------------------
# A correlation is only published when it has enough paired days and a strong
# enough coefficient; below that it is noise wearing a decimal point.
CORRELATION_WINDOW_DAYS = 120
CORRELATION_MIN_DAYS = 14
CORRELATION_MIN_R = 0.35
CORRELATION_MAX_FINDINGS = 4

# The daily channels a correlation may be computed over, named the way the
# sentence around them reads. A pair is only tested when both sides are measured.
CORRELATION_METRICS = {
    "hrv": "overnight HRV (ms)",
    "rhr": "resting heart rate (bpm)",
    "sleep_score": "sleep score",
    "deep_pct": "deep-sleep share (%)",
    "rem_pct": "REM-sleep share (%)",
    "sleep_hours": "sleep duration (hours)",
    "sleep_stress": "nightly stress (/100)",
    "respiration": "breathing rate (brpm)",
    "steps": "daily steps",
    "spo2": "blood oxygen (%)",
}

# Outdoor temperature and humidity are deliberately absent above. They are real
# data (the device's own weather station), but Garmin serves them per activity, so
# a daily series would cost one API call per day and could never honestly reach
# CORRELATION_MIN_DAYS. They appear on the climate card, where they are current
# conditions, rather than as a correlation the data cannot support.

# Curated plain-English meaning for the pairs worth explaining, keyed unordered.
PAIR_NOTES = {
    ("hrv", "sleep_score"): (
        "Recovery and sleep are the same story told twice: the nights your sleep scores climbed are the nights "
        "your nervous system had more room to recover."
    ),
    ("hrv", "rhr"): (
        "The two classic recovery dials moved together here. The usual reading is that a higher HRV comes with a "
        "lower resting heart rate, so a negative relationship between them is the expected one."
    ),
    ("deep_pct", "hrv"): (
        "Deep sleep is when tissue repair and parasympathetic recovery happen, so a stronger deep-sleep share "
        "tending to follow a higher HRV is the physiological result you would hope to see."
    ),
    ("rhr", "sleep_hours"): (
        "Sleep length and resting heart rate move against each other: shorter nights leave a higher resting "
        "heart rate the next morning."
    ),
    ("rhr", "steps"): (
        "More movement on a day, and a lower heart rate at rest that night, is the training effect showing up "
        "in your own numbers."
    ),
    ("respiration", "hrv"): (
        "Breathing rate and HRV are both autonomic signals, so when one moves the other tends to follow."
    ),
    ("sleep_stress", "hrv"): (
        "Overnight stress and HRV pull against each other: more autonomic arousal during sleep leaves less "
        "recovery capacity in the morning."
    ),
    ("spo2", "hrv"): (
        "Oxygen saturation and HRV are both measured while you sleep, so a night that is harder on one usually "
        "reads harder on the other."
    ),
    ("hrv", "steps"): (
        "Harder days demand more overnight repair, so the relationship between movement and recovery shows up "
        "in the pairing of these two."
    ),
}


# --- resolvers -------------------------------------------------------------

def spo2_band(average):
    if average is None:
        return None
    if average >= SPO2_NORMAL_MIN:
        return "normal"
    if average >= SPO2_MILD_MIN:
        return "mild"
    return "low"


def heat_band(pct):
    if pct is None:
        return None
    if pct >= HEAT_ACCLIMATION_FULL_MIN:
        return "full"
    if pct >= HEAT_ACCLIMATION_PARTIAL_MIN:
        return "partial"
    return "none"


def intensity_band(weekly_minutes):
    if weekly_minutes is None:
        return None
    if weekly_minutes >= INTENSITY_MET_MIN:
        return "met"
    if weekly_minutes >= INTENSITY_PARTIAL_MIN:
        return "partial"
    return "low"


def bmi_band(bmi):
    if bmi is None:
        return None
    if bmi < BMI_NORMAL_MIN:
        return "under"
    if bmi < BMI_OVERWEIGHT_MIN:
        return "normal"
    if bmi < BMI_OBESE_MIN:
        return "overweight"
    return "obese"


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


def acwr_band(acwr):
    """The ACWR band key for a ratio."""
    if acwr < ACWR_SWEET_MIN:
        return "under"
    if acwr <= ACWR_SWEET_MAX:
        return "sweet"
    if acwr <= ACWR_SAFE_MAX:
        return "high"
    return "danger"


def acwr_workload_band(acwr):
    """The band's name -- the one set of words every ACWR surface shows."""
    return ACWR_BANDS[acwr_band(acwr)]["label"]


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
