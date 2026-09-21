"""Coaching: measured signals in, prescriptions out.

The other modules answer "what do my numbers say". This one answers "so what do I
do today", for each training domain the device actually records: strength,
running, walking, hiking, sleep and load management.

Two rules keep it honest. First, every card is built from sessions, nights and
readings that exist -- a domain with no measured history says so instead of
issuing a generic tip, and an unmeasurable number renders as `--` like every
other absent value on the dashboard. Second, every rule cites the study it came
from (``bio_policy.EVIDENCE``), including where the literature disagrees, so a
recommendation can be argued with rather than merely obeyed.

Nothing here performs I/O or knows about provenance; like ``bio_analytics`` it is
pure, and ``sync`` decides where the numbers come from.
"""

import statistics
from datetime import date, timedelta

import bio_policy as policy


# --- small shared helpers ---------------------------------------------------


def _num(value):
    """A float, or None when the device never recorded it."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_of(value):
    """The calendar date of a Garmin timestamp, or None."""
    if not value:
        return None
    try:
        return date(*(int(part) for part in str(value)[:10].split("-")))
    except (TypeError, ValueError):
        return None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _spread(values):
    """Standard deviation, or None below two measurements."""
    values = [v for v in values if v is not None]
    return statistics.stdev(values) if len(values) > 1 else None


def _clock(minutes):
    """Minutes past midnight as 24-hour clock text, wrapped into a day."""
    if minutes is None:
        return None
    total = int(round(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _grade(value):
    """A signed percentage as readable text."""
    if value is None:
        return "--"
    return f"{value:+.0f}%"


# --- sessions ---------------------------------------------------------------


def _classify(session):
    """Which coach a session belongs to, from the device's own activity type."""
    kind = (session.get("activityType") or "").lower()
    category = (session.get("category") or "").lower()
    if "hiking" in kind or "mountaineering" in kind or "trekking" in kind:
        return "hiking"
    if "walk" in kind:
        return "walking"
    if "strength" in kind or "training" in kind or category == "gym":
        return "strength"
    if "climb" in kind or "boulder" in kind:
        # Climbing is resistance work with its own recovery cost, so it counts
        # towards the strength frequency the athlete is already accumulating.
        return "strength"
    if any(word in kind for word in ("run", "treadmill", "cycl", "bike", "swim", "row", "elliptical")):
        return "endurance"
    return "other"


def _load(session):
    """Duration x average heart rate.

    Crude on purpose: it is measured on every session and needs no assumption
    about heart-rate zones the athlete never configured.
    """
    minutes = _num(session.get("duration_min")) or 0.0
    heart = _num(session.get("averageHR")) or policy.LOAD_FALLBACK_HR
    return minutes * heart


def _recent(sessions, today, days):
    """Sessions inside the trailing window, newest last."""
    cutoff = today - timedelta(days=days)
    return [
        (moment, session)
        for moment, session in sessions
        if moment is not None and cutoff <= moment <= today
    ]


def _session_dates(activities):
    dated = []
    for session in activities or []:
        dated.append((_date_of(session.get("startTimeLocal")), session))
    dated = [pair for pair in dated if pair[0] is not None]
    dated.sort(key=lambda pair: pair[0])
    return dated


def domain_summary(activities, today):
    """What each training domain looked like over the coaching window."""
    dated = _session_dates(activities)
    window = _recent(dated, today, policy.COACH_WINDOW_DAYS)
    week = _recent(dated, today, policy.COACH_RECENT_DAYS)

    by_domain = {}
    for key, _, _ in policy.COACH_DOMAINS:
        if key in ("sleep", "recovery"):
            continue
        in_window = [session for _, session in window if _classify(session) == key]
        in_week = [session for _, session in week if _classify(session) == key]
        gains = [_num(s.get("elevationGain")) or 0.0 for s in in_window]
        distances = [_num(s.get("distance_km")) or 0.0 for s in in_window]
        by_domain[key] = {
            "sessions": len(in_window),
            "sessions_7d": len(in_week),
            "minutes": round(sum(_num(s.get("duration_min")) or 0.0 for s in in_window), 1),
            "distance_km": round(sum(distances), 2),
            "gain_m": round(sum(gains)),
            "load": round(sum(_load(s) for s in in_window)),
            "per_week": round(len(in_window) / (policy.COACH_WINDOW_DAYS / 7.0), 1),
            "mean_minutes": round(_mean([_num(s.get("duration_min")) for s in in_window]) or 0.0, 1) if in_window else None,
            "mean_hr": round(_mean([_num(s.get("averageHR")) for s in in_window]) or 0.0) if in_window else None,
            "gain_per_km": (
                round(sum(gains) / sum(distances), 1) if sum(distances) > 0.5 else None
            ),
        }

    # Daily load, so the acute:chronic picture and the personal patterns use the
    # same numbers as the cards.
    daily_load = {}
    for moment, session in window:
        daily_load[moment] = daily_load.get(moment, 0.0) + _load(session)
    acute = [value for day, value in daily_load.items() if (today - day).days < 7]
    load_7d = round(sum(acute) / 7.0, 1)
    load_28d = round(sum(daily_load.values()) / float(policy.COACH_WINDOW_DAYS), 1)

    return {
        "window_days": policy.COACH_WINDOW_DAYS,
        "sessions": len(window),
        "domains": by_domain,
        "load_7d": load_7d,
        "load_28d": load_28d,
        "daily_load": {day.isoformat(): round(value) for day, value in sorted(daily_load.items())},
        "train_days": len(daily_load),
    }


def load_by_weekday(activities, today, days=None):
    """Total session load per weekday over a window.

    One owner for "which day carries the work", because both the rhythm pattern
    and the "train on your lightest day" prescription read it.
    """
    by_weekday = {}
    for moment, session in _recent(_session_dates(activities), today, days or policy.COACH_WINDOW_DAYS):
        by_weekday[moment.weekday()] = by_weekday.get(moment.weekday(), 0.0) + _load(session)
    return by_weekday


def _weekday_names():
    return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def walking_summary(steps_history, today):
    """Step volume over the coaching window, from the device's own totals."""
    series = []
    for record in steps_history or []:
        moment = _date_of(record.get("calendarDate") or record.get("date"))
        value = _num(record.get("totalSteps", record.get("steps")))
        if moment is not None:
            series.append((moment, value or 0.0))
    series.sort(key=lambda pair: pair[0])
    week = [(d, v) for d, v in series if 0 <= (today - d).days < 7]
    month = [(d, v) for d, v in series if 0 <= (today - d).days < policy.COACH_WINDOW_DAYS]
    return {
        "days_7d": len(week),
        "days_28d": len(month),
        "mean_7d": round(_mean([v for _, v in week]) or 0.0) if week else None,
        "mean_28d": round(_mean([v for _, v in month]) or 0.0) if month else None,
        "days_met": sum(1 for _, v in week if v >= policy.WALKING_STEPS_TARGET),
        "lowest_day": min(week, key=lambda pair: pair[1])[0].isoformat() if week else None,
        "lowest_steps": round(min(v for _, v in week)) if week else None,
        "target": policy.WALKING_STEPS_TARGET,
    }


def sleep_summary(sleep_history, whoop, baselines):
    """Sleep length, debt, bedtime regularity and stage mix."""
    nights = []
    for night in sleep_history or []:
        moment = _date_of(night.get("date"))
        total = _num(night.get("total_seconds")) or 0.0
        if moment is None or total <= 0:
            continue
        nights.append(
            {
                "date": moment,
                "hours": total / 3600.0,
                "score": _num(night.get("score")),
                "deep_pct": (_num(night.get("deep_seconds")) or 0.0) / total * 100.0,
                "rem_pct": (_num(night.get("rem_seconds")) or 0.0) / total * 100.0,
                "bedtime": _num(night.get("bedtime_minutes")),
            }
        )
    nights.sort(key=lambda night: night["date"])
    week = nights[-7:]
    bedtimes = [night["bedtime"] for night in nights[-14:] if night["bedtime"] is not None]
    median_bedtime = _median(bedtimes)
    return {
        "nights": len(nights),
        "mean_hours_7d": round(_mean([n["hours"] for n in week]) or 0.0, 2) if week else None,
        "debt_hours": _num((whoop or {}).get("accumulated_7d_debt_hours")),
        "recommended_bedtime": _num((whoop or {}).get("recommended_bedtime_minutes")),
        "bedtime_median": median_bedtime,
        "bedtime_clock": _clock(median_bedtime),
        "bedtime_spread_minutes": round(_spread(bedtimes) or 0.0) if bedtimes else None,
        "late_nights": (
            sum(1 for b in bedtimes if b > median_bedtime + policy.SLEEP_LATE_NIGHT_MINUTES)
            if bedtimes and median_bedtime is not None
            else 0
        ),
        "deep_pct_7d": round(_mean([n["deep_pct"] for n in week]) or 0.0, 1) if week else None,
        "rem_pct_7d": round(_mean([n["rem_pct"] for n in week]) or 0.0, 1) if week else None,
        "deep_pct_baseline": _num((baselines or {}).get("deep_sleep_pct")),
        "rem_pct_baseline": _num((baselines or {}).get("rem_sleep_pct")),
    }


# --- cards ------------------------------------------------------------------


def _card(key, **fields):
    """One coach card, with the domain's own name and science attached."""
    title, science = next(
        ((t, s) for k, t, s in policy.COACH_DOMAINS if k == key), (key.title(), "")
    )
    card = {
        "key": key,
        "title": title,
        "science": science,
        "measured": True,
        "tone": "cyan",
        "basis": "",
        "verdict": "",
        "plain": "",
        "action": "",
        "progression": "",
        "guardrail": "",
        "metrics": [],
        "evidence": [],
    }
    card.update(fields)
    return card


def _metric(label, value, tone="slate"):
    return {"label": label, "value": value, "tone": tone}


def strength_card(domain, lightest_day):
    stats = domain["strength"]
    if not stats["sessions"]:
        return _card(
            "strength",
            measured=False,
            tone="slate",
            basis=f"no resistance sessions in the last {policy.COACH_WINDOW_DAYS} days",
            verdict="No strength session is in your own record, so there is nothing to prescribe from.",
            plain="this card fills itself in from your first logged session, rather than guessing a routine",
            action="Log one session -- even 20 minutes -- and the next run turns this into a real prescription.",
            progression="--",
            guardrail="--",
            evidence=policy.evidence("strength_frequency"),
        )

    rate = stats["per_week"]
    target = policy.STRENGTH_TARGET_PER_WEEK
    weeks = policy.COACH_WINDOW_DAYS / 7.0
    light_day = lightest_day
    tone = "green" if rate >= target else "amber"
    if rate >= target:
        action = (
            f"Hold {rate:.1f} sessions a week. Add the third only when two consecutive weeks land at two or more, "
            "and add reps or load before you add a whole session."
        )
        verdict = f"You averaged {rate:.1f} strength sessions a week over {policy.COACH_WINDOW_DAYS} days."
    else:
        slot = f" on {light_day}" if light_day else " on your lightest day"
        action = (
            f"Add one session in the next seven days{slot}: that takes you to "
            f"{(stats['sessions'] + 1) / weeks:.1f} a week, and the second weekly session is where most of the "
            f"difference between training once and training twice shows up."
        )
        verdict = (
            f"You averaged {rate:.1f} strength sessions a week over {policy.COACH_WINDOW_DAYS} days, "
            f"against a target of {target:.0f}."
        )
    return _card(
        "strength",
        tone=tone,
        basis=f"{stats['sessions']} sessions in {policy.COACH_WINDOW_DAYS} days",
        verdict=verdict,
        plain=(
            f"you lift about {rate:.1f} times a week; twice a week grows more muscle than once, "
            "so the cheapest gain is one extra session"
        ),
        action=action,
        progression=(
            f"Next two weeks: {target:.0f} sessions a week at the same effort. "
            f"Weeks three and four: add the {policy.STRENGTH_STRETCH_PER_WEEK:.0f}rd only if the first two held."
        ),
        guardrail=(
            f"Skip the session on a red readiness day, and hold volume when today's load ratio sits above "
            f"{policy.ACWR_SAFE_MAX} -- that is the week new tissue is least able to absorb it."
        ),
        metrics=[
            _metric("Sessions / week", f"{rate:.1f}", tone),
            _metric("Sessions logged", stats["sessions"]),
            _metric("Typical length", f"{stats['mean_minutes']:.0f} min" if stats["mean_minutes"] else "--"),
            _metric("Minutes trained", f"{stats['minutes']:.0f} min"),
        ],
        evidence=policy.evidence("strength_frequency", "load_ratio", "hrv_guided"),
    )


def endurance_card(domain, fitness):
    stats = domain["endurance"]
    band = (fitness or {}).get("acwr_band")
    band_label = (fitness or {}).get("acwr_band_label")
    acwr = (fitness or {}).get("acwr")
    weekly_minutes = stats["minutes"] / (policy.COACH_WINDOW_DAYS / 7.0)
    if not stats["sessions"]:
        return _card(
            "endurance",
            measured=False,
            tone="slate",
            basis=f"no running or hard cardio in the last {policy.COACH_WINDOW_DAYS} days",
            verdict="No running, cycling or swimming session is in your record for this window.",
            plain="there is no aerobic history to coach here, so the card stays empty rather than inventing one",
            action="Log one run and this becomes a pace, volume and intensity plan.",
            progression="--",
            guardrail="--",
            evidence=policy.evidence("intensity_guideline", "load_ratio"),
        )

    at_floor = weekly_minutes >= policy.INTENSITY_GOAL_MINUTES
    if band in ("high", "danger"):
        tone = "rose" if band == "danger" else "amber"
        action = (
            "Cap the volume this week: keep one easy session and replace the hardest with an easy 25 minutes. "
            f"Your ratio is {acwr} ({band_label})."
        )
    elif band == "under":
        tone = "green"
        action = (
            f"Add one easy 30-minute session before the weekend. Your ratio is {acwr} "
            f"({band_label}), which means this week has room in it."
        )
    else:
        tone = "green" if at_floor else "amber"
        action = (
            "Hold volume and keep the hard part short: one longer easy session, one harder session, the rest easy."
            + (f" Ratio {acwr} ({band_label})." if acwr is not None else "")
        )
    return _card(
        "endurance",
        tone=tone,
        basis=f"{stats['sessions']} sessions, {stats['minutes']:.0f} min in {policy.COACH_WINDOW_DAYS} days",
        verdict=(
            f"You ran or rode {weekly_minutes:.0f} minutes a week across {stats['sessions']} sessions "
            + (f"(about {stats['mean_minutes']:.0f} minutes each), " if stats["mean_minutes"] else "")
            + f"{'above' if at_floor else 'below'} the {policy.INTENSITY_GOAL_MINUTES}-minute weekly floor."
        ),
        plain=(
            f"you get about {weekly_minutes:.0f} hard-cardio minutes a week; the health floor is "
            f"{policy.INTENSITY_GOAL_MINUTES}, and the fitness gains come from adding easy minutes rather than "
            "turning every run into a race"
        ),
        action=action,
        progression=(
            "Add no more than one session or about 10% of weekly minutes at a time, then hold it for two weeks "
            "before the next increase."
        ),
        guardrail=(
            f"On a day when HRV sits below your baseline, keep the session but make it easy: that is what "
            f"HRV-guided prescription buys you. Skip hard intervals outright if readiness is red."
        ),
        metrics=[
            _metric("Minutes / week", f"{weekly_minutes:.0f}", tone),
            _metric("Sessions", stats["sessions"]),
            _metric("Distance", f"{stats['distance_km']:.1f} km"),
            _metric("Load ratio", str(acwr) if acwr is not None else "--", tone),
        ],
        evidence=policy.evidence("load_ratio", "hrv_guided", "intensity_guideline"),
    )


def walking_card(walking):
    if not walking["days_7d"]:
        return _card(
            "walking",
            measured=False,
            tone="slate",
            basis="no step totals in the last 7 days",
            verdict="The step totals did not arrive for this window.",
            plain="nothing to coach until the device reports daily steps again",
            action="--",
            progression="--",
            guardrail="--",
            evidence=policy.evidence("steps_mortality"),
        )
    mean_7d = walking["mean_7d"] or 0
    met = walking["days_met"]
    tone = "green" if met >= policy.WALKING_DAYS_MET_TARGET else "amber"
    trend = None
    if walking["mean_28d"]:
        trend = (mean_7d - walking["mean_28d"]) / walking["mean_28d"] * 100.0
    if met >= policy.WALKING_DAYS_MET_TARGET:
        action = (
            f"Hold it: {met} of the last 7 days cleared {policy.WALKING_STEPS_TARGET:,} steps. "
            "The extra health return above this level is small, so spend the time on strength or hills instead."
        )
    else:
        action = (
            f"Add one 15-minute walk on your two lowest days (your weakest day was {walking['lowest_steps']:,} steps "
            f"on {walking['lowest_day']}). That is the shortest path to {policy.WALKING_DAYS_MET_TARGET} days above "
            f"{policy.WALKING_STEPS_TARGET:,}."
        )
    return _card(
        "walking",
        tone=tone,
        basis=f"{walking['days_7d']} days of step totals",
        verdict=(
            f"You averaged {mean_7d:,.0f} steps a day over the last week, clearing "
            f"{policy.WALKING_STEPS_TARGET:,} on {met} of 7 days."
        ),
        plain=(
            f"you walk about {mean_7d:,.0f} steps a day; the mortality curve flattens near "
            f"{policy.WALKING_STEPS_TARGET:,}, so the goal is consistency rather than a bigger number"
        ),
        action=action,
        progression=(
            "Raise the floor rather than the average: get the weakest two days of the week above the target "
            "before chasing a new daily best."
        ),
        guardrail=(
            "Steps are a floor, not a training session -- do not let a long walk eat the recovery a hard "
            "session needs."
        ),
        metrics=[
            _metric("Steps / day", f"{mean_7d:,.0f}", tone),
            _metric("Days on target", f"{met} of 7"),
            _metric("28-day average", f"{walking['mean_28d']:,}" if walking["mean_28d"] else "--"),
            _metric("Trend", f"{trend:+.0f}%" if trend is not None else "--"),
        ],
        evidence=policy.evidence("steps_mortality", "intensity_guideline"),
    )


def hiking_card(domain, environment):
    stats = domain["hiking"]
    heat = (environment or {}).get("heat_label")
    acclimation = (environment or {}).get("heat_acclimation_pct")
    if not stats["sessions"]:
        return _card(
            "hiking",
            measured=False,
            tone="slate",
            basis=f"no hiking sessions in the last {policy.COACH_WINDOW_DAYS} days",
            verdict="No hike with a logged climb is in your record for this window.",
            plain="hill training has no history here yet, so there is no climb to progress from",
            action="Log one hike with elevation data and this becomes a hill-fitness plan.",
            progression="--",
            guardrail="--",
            evidence=policy.evidence("grade_cost", "heat_acclimation"),
        )
    gain_per_km = stats["gain_per_km"]
    steep = gain_per_km is not None and gain_per_km >= policy.HIKING_GAIN_PER_KM_STRONG
    if gain_per_km is None:
        action = "Your hikes carry distance but no elevation record, so climb cannot be graded yet."
        tone = "amber"
    elif steep:
        tone = "green"
        action = (
            f"Your climbs run {gain_per_km:.0f} m of gain per km, which is steep ground. Progress the vertical, "
            f"not the distance: add about 100 m of climb a week and keep the descents controlled."
        )
    else:
        tone = "amber"
        action = (
            f"Your hikes average {gain_per_km:.0f} m of gain per km -- mostly flat walking. If you want hill "
            "fitness, choose a hillier route rather than a longer one."
        )
    return _card(
        "hiking",
        tone=tone,
        basis=f"{stats['sessions']} hikes, {stats['gain_m']:,} m climbed",
        verdict=(
            f"{stats['sessions']} hikes in {policy.COACH_WINDOW_DAYS} days climbed {stats['gain_m']:,} m over "
            f"{stats['distance_km']:.1f} km"
            + (f", averaging {gain_per_km:.0f} m per km." if gain_per_km is not None else ".")
        ),
        plain=(
            "climbing costs far more energy than the same distance on the flat, so a hill session is measured by "
            "its ascent -- that is the number to grow"
        ),
        action=action,
        progression=(
            "Grow total climb by roughly a tenth every two weeks, and keep one shorter, steeper session for the "
            "downhill legs."
        ),
        guardrail=(
            f"Heat adaptation reads {acclimation if acclimation is not None else '--'}% ({heat or '--'}): "
            "hike early, carry more fluid than feels necessary, and treat humidity as extra distance."
        ),
        metrics=[
            _metric("Total climb", f"{stats['gain_m']:,} m"),
            _metric("Gain / km", f"{gain_per_km:.0f} m" if gain_per_km is not None else "--", tone),
            _metric("Hikes", stats["sessions"]),
            _metric("Heat adaptation", f"{acclimation}%" if acclimation is not None else "--"),
        ],
        evidence=policy.evidence("grade_cost", "heat_acclimation", "fluid"),
    )


def sleep_card(sleep):
    if not sleep["nights"]:
        return _card(
            "sleep",
            measured=False,
            tone="slate",
            basis="no nights recorded in the window",
            verdict="No sleep record arrived for this window.",
            plain="nothing to coach until the device reports nights again",
            action="--",
            progression="--",
            guardrail="--",
            evidence=policy.evidence("sleep_extension", "sleep_regularity"),
        )
    debt = sleep["debt_hours"]
    spread = sleep["bedtime_spread_minutes"]
    mean_hours = sleep["mean_hours_7d"] or 0.0
    actions = []
    if debt is not None and debt >= policy.SLEEP_DEBT_ACTION_HOURS:
        target = _clock(sleep["recommended_bedtime"])
        actions.append(
            f"Repay {debt:.1f} h of debt by extending the next three nights"
            + (f", lights out by {target}" if target else "")
            + "."
        )
    if spread is not None and spread > policy.SLEEP_REGULARITY_SPREAD_MINUTES:
        median = sleep["bedtime_median"]
        window_start = _clock(median - 30) if median is not None else None
        window_end = _clock(median + 30) if median is not None else None
        actions.append(
            f"Anchor your bedtime inside {window_start}-{window_end}: it has been moving by about "
            f"{spread:.0f} minutes, and regularity predicts health outcomes more strongly than duration does."
        )
    if not actions:
        actions.append(
            "Hold this: length and timing are both steady, so the return now comes from protecting the sleep "
            "you already get on training days."
        )
    deep_gap = None
    if sleep["deep_pct_7d"] is not None and sleep["deep_pct_baseline"]:
        deep_gap = sleep["deep_pct_7d"] - sleep["deep_pct_baseline"]
    tone = "green" if (debt is None or debt < policy.SLEEP_DEBT_ACTION_HOURS) and (spread is None or spread <= policy.SLEEP_REGULARITY_SPREAD_MINUTES) else "amber"
    return _card(
        "sleep",
        tone=tone,
        basis=f"{sleep['nights']} nights",
        verdict=(
            f"You averaged {mean_hours:.1f} h a night over the last week"
            + (f", carrying {debt:.1f} h of debt" if debt is not None else "")
            + (f", with bedtime moving about {spread:.0f} minutes night to night." if spread is not None else ".")
        ),
        plain=(
            f"you sleep about {mean_hours:.1f} hours a night"
            + (f" and owe yourself roughly {debt:.1f} hours this week" if debt is not None else "")
            + "; going to bed at the same time is worth as much as going to bed earlier"
        ),
        action=" ".join(actions),
        progression=(
            "Add 15 minutes to the front of the night for a week, not an hour at once -- it is the bedtime that "
            "moves, not the wake time."
        ),
        guardrail=(
            "Do not trade sleep for a session: the performance cost of a short night is larger than the gain "
            "from the extra training."
        ),
        metrics=[
            _metric("Sleep / night", f"{mean_hours:.1f} h", tone),
            _metric("7-day debt", f"{debt:.1f} h" if debt is not None else "--"),
            _metric("Bedtime", sleep["bedtime_clock"] or "--"),
            _metric("Bedtime spread", f"{spread:.0f} min" if spread is not None else "--"),
            _metric(
                "Deep sleep",
                f"{sleep['deep_pct_7d']:.1f}%"
                + (f" ({_grade(deep_gap)})" if deep_gap is not None else "")
                if sleep["deep_pct_7d"] is not None
                else "--",
            ),
        ],
        evidence=policy.evidence("sleep_extension", "sleep_regularity"),
    )


def _hrv_reading(today, readiness):
    """The HRV band's own label and tone, resolved by policy rather than re-derived."""
    hrv = _num((today or {}).get("hrv_last_night"))
    baseline = _num((readiness or {}).get("hrv_baseline"))
    if hrv is None or not baseline:
        return None, None
    band = policy.hrv_band(hrv, baseline)
    return policy.HRV_BANDS[band]["label"], policy.HRV_BANDS[band]["tone"]


def recovery_card(fitness, readiness, today):
    band = (readiness or {}).get("band") or ""
    tone = (readiness or {}).get("tone") or "cyan"
    acwr = (fitness or {}).get("acwr")
    acwr_band = (fitness or {}).get("acwr_band")
    acwr_label = (fitness or {}).get("acwr_band_label")
    hrv_band, _ = _hrv_reading(today, readiness)
    score = (readiness or {}).get("score")
    if band.upper() in ("RED", "REST") or acwr_band == "danger":
        action = "Today is easy or off. No hard intervals, no heavy lifting -- walk, or take the day."
    elif band.upper() in ("YELLOW", "READY") and acwr_band in ("high",):
        action = "Today: easy aerobic only. Keep the heart rate conversational and cap it at 30 minutes."
    elif tone == "green" and acwr_band in ("sweet", "under"):
        action = "Today is a green light: this is the session to spend on the hardest work of your week."
    else:
        action = "Today: moderate. One solid session is fine; leave the maximal effort for a green day."
    return _card(
        "recovery",
        tone=tone,
        basis=f"readiness {score if score is not None else '--'} | ratio {acwr if acwr is not None else '--'}",
        verdict=(
            f"Readiness is {score if score is not None else '--'}"
            + (f" ({band})" if band else "")
            + (f", load ratio {acwr} ({acwr_label})" if acwr is not None else "")
            + (f", HRV {hrv_band}" if hrv_band else "")
            + "."
        ),
        plain=(
            "this is the decision that gates every other card: the recovery reading says how much today can hold, "
            "so it is read before the training ones"
        ),
        action=action,
        progression=(
            "Keep the ratio inside 0.8-1.3: raise load only after a week that ended green, and back off the "
            "moment a red morning follows a hard day."
        ),
        guardrail=(
            "Two hard days in a row is the pattern that most often precedes a dip in HRV -- when the second "
            "morning is below baseline, make that day easy whatever the plan said."
        ),
        metrics=[
            _metric("Readiness", str(score) if score is not None else "--", tone),
            _metric("Load ratio", str(acwr) if acwr is not None else "--", tone),
            _metric("Ratio band", acwr_label or "--"),
            _metric("HRV band", hrv_band or "--"),
        ],
        evidence=policy.evidence("hrv_guided", "load_ratio"),
    )


# --- personal patterns ------------------------------------------------------


def _pattern(key, label, finding, plain, pairs, tone="cyan"):
    return {
        "key": key,
        "label": label,
        "finding": finding,
        "plain": plain,
        "pairs": pairs,
        "tone": tone,
        "kind": "association",
    }


def personal_patterns(activities, sleep_history, hrv_history, steps_history, today):
    """How this athlete's own signals move together, with the day counts.

    These are associations measured on the athlete's own days, not mechanisms: the
    note attached to each one says so, exactly as the correlation lab does. They
    read the long window, because a month rarely holds enough hard days to split.
    """
    patterns = []
    span = policy.PATTERN_WINDOW_DAYS
    dated = _session_dates(activities)
    window = _recent(dated, today, span)
    day_load = {}
    for moment, session in window:
        day_load[moment] = day_load.get(moment, 0.0) + _load(session)
    positive = [value for value in day_load.values() if value > 0]
    hard_threshold = _median(positive)

    hrv_by_day = {}
    for record in hrv_history or []:
        moment = _date_of(record.get("calendarDate"))
        value = _num(record.get("lastNightAvg"))
        if moment is not None and value is not None:
            hrv_by_day[moment] = value

    # A day only counts when the day before it is inside the pattern window, so
    # "after a hard day" and "after a rest day" are compared over the same span.
    window_start = today - timedelta(days=span)
    if hard_threshold:
        after_hard, after_rest = [], []
        for moment, value in hrv_by_day.items():
            previous = moment - timedelta(days=1)
            if not (window_start <= previous <= today):
                continue
            load = day_load.get(previous, 0.0)
            if load >= hard_threshold:
                after_hard.append(value)
            elif load == 0:
                after_rest.append(value)
        pairs = len(after_hard) + len(after_rest)
        if len(after_hard) >= 6 and len(after_rest) >= 6 and pairs >= policy.PATTERN_MIN_PAIRS:
            mean_hard, mean_rest = _mean(after_hard), _mean(after_rest)
            change = (mean_hard - mean_rest) / mean_rest * 100.0
            patterns.append(
                _pattern(
                    "hrv_after_load",
                    "Your HRV after a hard day",
                    f"After a day above your median load, overnight HRV averages {mean_hard:.1f} ms against "
                    f"{mean_rest:.1f} ms after a rest day ({_grade(change)}), over {pairs} paired days.",
                    "this is your own recovery signature: it tells you how much a hard day actually costs you "
                    "before the next one",
                    pairs,
                    "rose" if change < -8 else "cyan",
                )
            )

    nights = []
    for night in sleep_history or []:
        moment = _date_of(night.get("date"))
        score = _num(night.get("score"))
        bedtime = _num(night.get("bedtime_minutes"))
        if moment is None or score is None:
            continue
        nights.append((moment, score, bedtime))
    bedtimes = [bedtime for _, _, bedtime in nights if bedtime is not None]
    median_bedtime = _median(bedtimes)
    if median_bedtime is not None:
        late = [score for _, score, bedtime in nights if bedtime is not None and bedtime > median_bedtime + policy.SLEEP_LATE_NIGHT_MINUTES]
        on_time = [score for _, score, bedtime in nights if bedtime is not None and abs(bedtime - median_bedtime) <= 30]
        if len(late) >= 6 and len(on_time) >= 6:
            change = (_mean(late) - _mean(on_time)) / _mean(on_time) * 100.0
            patterns.append(
                _pattern(
                    "sleep_after_late_bed",
                    "Sleep score after a late night",
                    f"Nights that started more than {policy.SLEEP_LATE_NIGHT_MINUTES} minutes after your usual "
                    f"bedtime scored {_mean(late):.0f} against {_mean(on_time):.0f} on your regular nights "
                    f"({_grade(change)}), over {len(late) + len(on_time)} nights.",
                    "your sleep scores are sensitive to when you start, not only how long you stay",
                    len(late) + len(on_time),
                    "amber" if change < -5 else "cyan",
                )
            )

    steps_by_day = {}
    for record in steps_history or []:
        moment = _date_of(record.get("calendarDate") or record.get("date"))
        value = _num(record.get("totalSteps", record.get("steps")))
        if moment is not None and value is not None:
            steps_by_day[moment] = value
    if hard_threshold and steps_by_day:
        after_hard, after_rest = [], []
        for moment, value in steps_by_day.items():
            previous = moment - timedelta(days=1)
            if not (window_start <= previous <= today):
                continue
            load = day_load.get(previous, 0.0)
            if load >= hard_threshold:
                after_hard.append(value)
            elif load == 0:
                after_rest.append(value)
        if len(after_hard) >= 6 and len(after_rest) >= 6:
            change = (_mean(after_hard) - _mean(after_rest)) / _mean(after_rest) * 100.0
            patterns.append(
                _pattern(
                    "steps_after_load",
                    "Movement the day after training",
                    f"You take {_mean(after_hard):,.0f} steps the day after a hard session against "
                    f"{_mean(after_rest):,.0f} after a rest day ({_grade(change)}), over "
                    f"{len(after_hard) + len(after_rest)} days.",
                    "a big drop here means you are resting hard rather than moving easily, which changes what the "
                    "day after training should look like",
                    len(after_hard) + len(after_rest),
                    "cyan",
                )
            )

    # Weekly rhythm: which day carries the load, and which day rarely does.
    if day_load:
        weeks = max(1, span // 7)
        by_weekday = load_by_weekday(activities, today, span)
        heaviest = max(by_weekday, key=by_weekday.get)
        total = sum(by_weekday.values())
        names = _weekday_names()
        training_days = len({moment for moment, _ in window})
        minutes = sum(_num(session.get("duration_min")) or 0.0 for _, session in window)
        patterns.append(
            _pattern(
                "weekly_rhythm",
                "Your training rhythm",
                f"{names[heaviest]} carries the most load ({by_weekday[heaviest] / total * 100:.0f}% of it), "
                f"across {training_days} training days in the last {span} -- "
                f"{training_days / weeks:.1f} sessions and {minutes / weeks:.0f} minutes of measured "
                f"training a week.",
                "knowing which day already carries your hardest work is what stops a new session landing on top of it",
                weeks,
                "cyan",
            )
        )

    # Aerobic efficiency: metres travelled per heartbeat, early versus recent.
    runs = []
    for moment, session in window:
        if _classify(session) != "endurance":
            continue
        distance = _num(session.get("distance_km")) or 0.0
        minutes = _num(session.get("duration_min")) or 0.0
        heart = _num(session.get("averageHR"))
        if distance >= 2.0 and minutes > 0 and heart:
            runs.append((moment, distance * 1000.0 / (minutes * heart)))
    if len(runs) >= policy.PATTERN_MIN_RUNS:
        midpoint = len(runs) // 2
        early, recent = runs[:midpoint], runs[midpoint:]
        if early and recent:
            change = (_mean([v for _, v in recent]) - _mean([v for _, v in early])) / _mean([v for _, v in early]) * 100.0
            patterns.append(
                _pattern(
                    "aerobic_efficiency",
                    "Distance per heartbeat",
                    f"Your metres per heartbeat went from {_mean([v for _, v in early]):.2f} to "
                    f"{_mean([v for _, v in recent]):.2f} across {len(runs)} runs ({_grade(change)}), comparing "
                    "your earlier runs with your most recent ones.",
                    "this is the cleanest aerobic-fitness signal in your own data: more distance for the same "
                    "heart rate means the engine grew",
                    len(runs),
                    "green" if change > 2 else ("amber" if change < -2 else "cyan"),
                )
            )

    return patterns


# --- assembly ---------------------------------------------------------------


def _focus(cards, readiness_tone):
    """The single action worth doing today.

    Recovery gates everything: a red or amber morning outranks any volume target,
    because training through it is the one choice the other cards cannot undo.
    """
    severity = {"rose": 3, "amber": 2, "cyan": 1, "green": 0, "slate": 0}
    order = {key: index for index, key in enumerate(policy.COACH_PRIORITY)}
    scored = []
    for card in cards:
        if not card["measured"]:
            continue
        score = severity.get(card["tone"], 0) * 2
        if card["key"] == "recovery" and readiness_tone in ("rose", "amber"):
            score += 3
        # Policy decides which domain wins a tie, so the focus is a stated
        # priority rather than the order the cards happen to be built in.
        scored.append((score, -order.get(card["key"], len(order)), card))
    if not scored:
        return None
    scored.sort(key=lambda triple: (-triple[0], -triple[1]))
    best = scored[0][2]
    return {
        "key": best["key"],
        "title": best["title"],
        "tone": best["tone"],
        "action": best["action"],
        "plain": best["plain"],
        "basis": best["basis"],
    }


def build_coaching(
    today_str,
    activities,
    steps_history,
    sleep_history,
    whoop,
    baselines,
    fitness,
    readiness,
    environment,
    today_snapshot,
    hrv_history,
):
    """Every coach card, the pattern panel and today's focus, from measured data."""
    today = _date_of(today_str)
    if today is None:
        return {"available": False, "reason": "no date"}

    domain = domain_summary(activities, today)
    walking = walking_summary(steps_history, today)
    sleep = sleep_summary(sleep_history, whoop, baselines)
    patterns = personal_patterns(activities, sleep_history, hrv_history, steps_history, today)

    lightest = None
    if domain["sessions"]:
        by_weekday = load_by_weekday(activities, today)
        lightest = _weekday_names()[min(range(7), key=lambda day: by_weekday.get(day, 0.0))]

    domains = domain["domains"]
    recovery = recovery_card(fitness, readiness, today_snapshot)
    cards = [
        recovery,
        strength_card(domains, lightest),
        endurance_card(domains, fitness),
        walking_card(walking),
        hiking_card(domains, environment),
        sleep_card(sleep),
    ]

    return {
        "available": True,
        "generated_for": today_str,
        "window_days": policy.COACH_WINDOW_DAYS,
        "focus": _focus(cards, (readiness or {}).get("tone") or "cyan"),
        "cards": cards,
        "patterns": patterns,
        "week": {
            "sessions": domain["sessions"],
            "train_days": domain["train_days"],
            "minutes": round(sum(_num(s.get("duration_min")) or 0.0 for _, s in _recent(_session_dates(activities), today, policy.COACH_WINDOW_DAYS))),
            "steps_mean_7d": walking["mean_7d"],
            "steps_days_met": walking["days_met"],
            "sleep_hours_7d": sleep["mean_hours_7d"],
            "lightest_day": lightest,
        },
        # The same caveat the correlation lab publishes, for the same reason.
        "pattern_caveat": (
            "Each pattern is computed from your own paired days, so it describes how your signals have moved "
            "-- not why."
        ),
    }
