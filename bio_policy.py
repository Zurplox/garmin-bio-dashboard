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
# The window overnight HRV is judged against. One constant, because the KPI card
# and the chart's per-night band both mean this same window by "baseline".
HRV_BASELINE_DAYS = 30
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


# --- coaching ---------------------------------------------------------------
# Everything above answers "what do my numbers say". Coaching answers "so what do
# I do today", so each domain below is one prescription built from the same
# parts: the reading, the action, how it progresses, when to back off, and the
# study the rule came from. The thresholds and the plain-English lines live here
# with every other policy value, so the coach cannot invent either.

COACH_WINDOW_DAYS = 28  # the window a weekly session rate is judged over
COACH_RECENT_DAYS = 7
# Personal patterns are chronic, so they read the same long window as the
# correlation lab: a 28-day slice rarely holds enough hard days to compare.
PATTERN_WINDOW_DAYS = 120
COACH_MIN_SESSIONS = 3  # below this a weekly rate is noise, not a rate

# Session load is duration x average heart rate: crude, but it is measured on
# every session and needs no assumption about zones the athlete never set.
LOAD_FALLBACK_HR = 120.0

# Strength: the major muscle groups want training at least twice a week.
STRENGTH_TARGET_PER_WEEK = 2.0
STRENGTH_STRETCH_PER_WEEK = 3.0

# Hard cardio: the public-health floor is 150 moderate minutes a week.
ENDURANCE_TARGET_PER_WEEK = 3.0
ENDURANCE_MIN_MINUTES = 20.0

# Walking: the mortality curve bends flat around 8,000 steps for this age band.
WALKING_STEPS_TARGET = 8000
WALKING_DAYS_MET_TARGET = 5  # of the last 7

# Hiking: gain per kilometre is what turns a walk into a climb.
HIKING_GAIN_PER_KM_MIN = 25.0
HIKING_GAIN_PER_KM_STRONG = 50.0
HIKING_GAIN_PER_HOUR_NOTABLE = 300.0

# Sleep: the debt worth acting on, and the bedtime spread that counts as regular.
SLEEP_DEBT_ACTION_HOURS = 2.5
SLEEP_REGULARITY_SPREAD_MINUTES = 45.0
SLEEP_LATE_NIGHT_MINUTES = 45

# Heat: adaptations decay roughly 2.5% a day without exposure.
HEAT_DECAY_PER_DAY = 0.025
HEAT_INDUCTION_DAYS = 5

# A personal pattern is only published once this many paired days support it.
PATTERN_MIN_PAIRS = 12
PATTERN_MIN_RUNS = 6

# Which domain wins when two cards are equally urgent. Recovery first because it
# gates the rest, then sleep because everything else is repaid in it, then the
# training that needs the most recovery to absorb.
COACH_PRIORITY = ("recovery", "sleep", "strength", "endurance", "hiking", "walking")

# What each coach card is called, and the technical name of what it tunes.
COACH_DOMAINS = (
    ("strength", "Strength & Muscle", "Resistance-training frequency"),
    ("endurance", "Running & Hard Cardio", "Aerobic load and intensity"),
    ("walking", "Walking & Daily Movement", "Step volume"),
    ("hiking", "Hiking & Hills", "Grade-adjusted load"),
    ("sleep", "Sleep", "Sleep extension and regularity"),
    ("recovery", "Recovery & Load Management", "Autonomic readiness and ACWR"),
)

# The studies the coaching rules are drawn from. Each anchor names what it
# supports, what it found and -- where the literature disagrees -- what it does
# not settle, because a coach that quotes only the convenient half of a debate is
# just an opinion with a citation.
EVIDENCE = {
    "load_ratio": {
        "claim": "Keep the week-on-week load ratio inside the 0.8-1.3 band.",
        "source": "Gabbett TJ. The training-injury prevention paradox. Br J Sports Med 2016;50:273-280.",
        "finding": "Sustaining an acute:chronic workload ratio near 0.8-1.3 was associated with the lowest injury risk; sharp spikes above it were not.",
        "caveat": "Contested ground. Zouhal et al. (2021) and Maupin et al. (2020) find the band plausible but the evidence inconsistent, so this app treats it as a guide rail rather than a verdict.",
    },
    "hrv_guided": {
        "claim": "Let the morning reading decide whether today is hard or easy.",
        "source": "Vesterinen V et al. Med Sci Sports Exerc 2016;48(7):1347-1354; Manresa-Rocamora A et al. Int J Environ Res Public Health 2021;18(19):10206.",
        "finding": "Prescribing hard days by HRV produced equal or better performance than a fixed plan and fewer negative responses in vagal HRV.",
        "caveat": "Duking et al. (2021) found the performance advantage small and in some analyses non-significant; the recovery benefit is the better-evidenced half.",
    },
    "sleep_extension": {
        "claim": "Repay sleep debt by extending the night, not by training through it.",
        "source": "Mah CD et al. The effects of sleep extension on the athletic performance of collegiate basketball players. Sleep 2011;34(7):943-950.",
        "finding": "Several weeks of extra sleep improved sprint times, shooting accuracy, reaction time and mood.",
        "caveat": "Small sample (11 players) and self-selected extension, so the size of the effect is less certain than its direction.",
    },
    "sleep_regularity": {
        "claim": "A steady bedtime is worth chasing even when the total is short.",
        "source": "Windred DP et al. Sleep regularity is a stronger predictor of mortality risk than sleep duration. Sleep 2024;47(1):zsad253.",
        "finding": "Across ~60,000 UK Biobank participants, sleep regularity predicted all-cause mortality more strongly than sleep duration did.",
        "caveat": "Observational: regular sleepers differ in other ways that no model fully removes.",
    },
    "strength_frequency": {
        "claim": "Train each major muscle group at least twice a week.",
        "source": "Schoenfeld BJ, Ogborn D, Krieger JW. Effects of resistance training frequency on measures of muscle hypertrophy. Sports Med 2016;46(11):1689-1697.",
        "finding": "When weekly volume was matched, training a muscle group twice or more per week produced more growth than once.",
        "caveat": "Total weekly volume still matters more than how it is split; frequency is the easier lever when only a few sessions happen.",
    },
    "steps_mortality": {
        "claim": "Walk towards 8,000 steps a day rather than 10,000 for its own sake.",
        "source": "Paluch AE et al. Daily steps and all-cause mortality: a meta-analysis of 15 international cohorts. Lancet Public Health 2022;7(3):e219-e228.",
        "finding": "Mortality risk fell with more daily steps and levelled off around 6,000-8,000 in older adults and 8,000-10,000 in younger ones.",
        "caveat": "Observational, and the plateau differs by age -- the step goal here is the 8,000 end of that range, not a hard threshold.",
    },
    "heat_acclimation": {
        "claim": "Heat fitness fades fast: it needs roughly weekly exposure to stay.",
        "source": "Daanen HAM, Racinais S, Periard JD. Heat acclimation decay and re-induction. Sports Med 2018;48(2):409-430.",
        "finding": "Five or more heat days produce stable adaptations, and without exposure end-exercise heart rate decays about 2.3% and core temperature about 2.6% per day.",
        "caveat": "Decay figures come mostly from laboratory protocols, so treat the rate as an order of magnitude.",
    },
    "grade_cost": {
        "claim": "Judge a hike by its climb, not its distance.",
        "source": "Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. Energy cost of walking and running at extreme uphill and downhill slopes. J Appl Physiol 2002;93(3):1039-1046.",
        "finding": "Metabolic cost rises steeply with gradient in both gaits, with the most economical mountain-path gradient around 0.20-0.30.",
        "caveat": "Measured on a treadmill; rough ground costs more than the same gradient on a belt.",
    },
    "fluid": {
        "claim": "Replace most of what a hot session costs you, and start hydrated.",
        "source": "Sawka MN et al. ACSM position stand: exercise and fluid replacement. Med Sci Sports Exerc 2007;39(2):377-390.",
        "finding": "Fluid losses above about 2% of body mass degrade performance; replacing the majority of sweat lost is the practical target.",
        "caveat": "Sweat rate varies several-fold between people and conditions, so the measured loss here is a better guide than the generic figure.",
    },
    "intensity_guideline": {
        "claim": "Aim for 150 moderate-intensity minutes a week as a floor.",
        "source": "WHO guidelines on physical activity and sedentary behaviour, 2020.",
        "finding": "150-300 minutes of moderate or 75-150 minutes of vigorous activity per week is the recommended range for adults.",
        "caveat": "This is a public-health floor for health outcomes, not a performance prescription for someone already training.",
    },
}


def evidence(*ids):
    """The citation records for a rule, ready to publish."""
    return [{"id": key, **EVIDENCE[key]} for key in ids if key in EVIDENCE]


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


def hrv_band_fields(hrv, baseline):
    """One night's HRV resolved into the word and tone every surface shows.

    A night with no measurement publishes nothing rather than a band, so a
    surface has to render the absence as absent instead of falling back to a
    reassuring word nobody measured.
    """
    if hrv is None or not baseline:
        return {}
    band = hrv_band(hrv, baseline)
    return {
        "band": band,
        "band_label": HRV_BANDS[band]["label"],
        "tone": HRV_BANDS[band]["tone"],
    }


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
