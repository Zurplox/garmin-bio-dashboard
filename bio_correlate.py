"""
Correlations between the athlete's own daily channels.

Numbers in, findings out: this module only computes relationships between series
that were already measured, and it refuses to publish one that its own thresholds
say is too thin. Nothing here fetches, formats for display, or writes prose that
policy did not supply.

Two refusals matter, because most "insights" dashboards are dishonest in the same
two ways:

* A coefficient needs enough paired days and a strong enough magnitude before it
  is a finding rather than noise wearing a decimal point (`CORRELATION_MIN_DAYS`,
  `CORRELATION_MIN_R`). The paired-day count travels with every finding, so a
  reader can see how much of the window actually backs it.
* Only the pairs policy curates are tested, not all 78 combinations of thirteen
  channels. Testing everything guarantees a publishable-looking `r` by chance
  alone, and there would be nothing truthful to say about the pair that won.

Correlation is not causation and the findings say so in words rather than in a
footnote, because the sentence is the part that gets read.
"""

import bio_policy as policy
from datetime import date, timedelta


def pearson(series_a, series_b):
    """Pearson r over the dates both channels were measured, plus that count.

    Returns (r, n) or (None, n). An `n` below the minimum is returned so the
    caller can report how little data a refused pair had.
    """
    dates = [d for d in series_a if d in series_b]
    pairs = []
    for date in dates:
        try:
            a = float(series_a[date])
            b = float(series_b[date])
        except (TypeError, ValueError):
            continue
        pairs.append((a, b))

    n = len(pairs)
    if n < policy.CORRELATION_MIN_DAYS:
        return None, n

    mean_a = sum(a for a, _ in pairs) / n
    mean_b = sum(b for _, b in pairs) / n
    covariance = sum((a - mean_a) * (b - mean_b) for a, b in pairs)
    variance_a = sum((a - mean_a) ** 2 for a, _ in pairs)
    variance_b = sum((b - mean_b) ** 2 for _, b in pairs)
    if variance_a <= 0 or variance_b <= 0:
        # A channel that never moved cannot be correlated with anything.
        return None, n

    return covariance / (variance_a**0.5 * variance_b**0.5), n


def _pair_note(key_a, key_b):
    return policy.PAIR_NOTES.get((key_a, key_b)) or policy.PAIR_NOTES.get((key_b, key_a)) or (
        "A pattern in your own data, not proof that one causes the other."
    )


def build_correlations(series, max_findings=None):
    """Rank the curated channel pairs by how strong the relationship is.

    `series` maps a `CORRELATION_METRICS` key to {date: value}. Every finding
    carries the plain-English reading of its pair from policy, the coefficient,
    and the number of paired days behind it.
    """
    max_findings = max_findings or policy.CORRELATION_MAX_FINDINGS
    findings = []
    tested = 0
    skipped = []

    for key_a, key_b in policy.PAIR_NOTES:
        if key_a not in series or key_b not in series:
            continue
        tested += 1
        r, n = pearson(series[key_a], series[key_b])
        if r is None or abs(r) < policy.CORRELATION_MIN_R:
            skipped.append({"pair": [key_a, key_b], "days": n, "r": None if r is None else round(r, 2)})
            continue
        label_a = policy.CORRELATION_METRICS[key_a]
        label_b = policy.CORRELATION_METRICS[key_b]
        findings.append(
            {
                "metric_a": key_a,
                "metric_b": key_b,
                "label_a": label_a,
                "label_b": label_b,
                "r": round(r, 2),
                "days": n,
                "strength": strength_word(r),
                "direction": "higher" if r > 0 else "lower",
                "note": _pair_note(key_a, key_b),
            }
        )

    findings.sort(key=lambda f: abs(f["r"]), reverse=True)
    return {
        "findings": findings[:max_findings],
        "tested_pairs": tested,
        "window_days": policy.CORRELATION_WINDOW_DAYS,
        "min_days": policy.CORRELATION_MIN_DAYS,
        "min_r": policy.CORRELATION_MIN_R,
        "caveat": policy.CORRELATION_CAVEAT,
        "skipped": skipped,
    }


def strength_word(r):
    """How a coefficient reads in a sentence."""
    magnitude = abs(r)
    if magnitude >= 0.7:
        return "strong"
    if magnitude >= 0.5:
        return "clear"
    return "modest"


def location_breakdown(location_days, metrics):
    """Home versus away averages for each metric that has enough nights on both sides.

    `location_days` is [{date, location}] from the activity feed, so "away" means
    the device recorded a session somewhere other than the athlete's most common
    location -- not a claim about where they slept. Only the mean difference is
    reported, and only when policy's minimum nights hold on both sides.
    """
    home = home_location(location_days)
    if not home:
        return None

    # Sessions the device recorded without a location are not a place, so they
    # count as neither home nor away. Treating them as "away" is what made the
    # first version of this comparison describe a trip that never happened.
    located = [e for e in location_days if e.get("location")]
    away_locations = {}
    for entry in located:
        if entry["location"] != home:
            away_locations[entry["location"]] = away_locations.get(entry["location"], 0) + 1
    if not away_locations:
        return None
    away = max(away_locations, key=lambda name: away_locations[name])
    away_dates = {e["date"] for e in located if e["location"] != home}
    home_dates = {e["date"] for e in located if e["location"] == home}

    comparisons = []
    for key in policy.TRAVEL_METRIC_ORDER:
        series = metrics.get(key)
        if not series:
            continue
        home_values = [v for d, v in series.items() if d in home_dates]
        away_values = [v for d, v in series.items() if d in away_dates]
        if len(home_values) < policy.TRAVEL_MIN_NIGHTS or len(away_values) < policy.TRAVEL_MIN_NIGHTS:
            continue
        home_mean = sum(home_values) / len(home_values)
        away_mean = sum(away_values) / len(away_values)
        comparisons.append(
            {
                "metric": key,
                "label": policy.CORRELATION_METRICS[key],
                "home_mean": round(home_mean, 1),
                "away_mean": round(away_mean, 1),
                "delta": round(away_mean - home_mean, 1),
                "home_days": len(home_values),
                "away_days": len(away_values),
            }
        )

    if not comparisons:
        return None
    available = len(comparisons)
    comparisons = comparisons[: policy.TRAVEL_MAX_COMPARISONS]
    return {
        "home_location": home,
        "comparisons_available": available,
        # The most-visited away place, named, while the averages below cover every
        # away day so a multi-city trip is not measured by one city's nights.
        "away_location": away,
        "away_days": len(away_dates),
        "away_sessions": sum(away_locations.values()),
        "away_location_count": len(away_locations),
        "home_days": len(home_dates),
        "comparisons": comparisons,
    }


def home_location(location_days):
    """The location the device recorded most often: where this athlete trains."""
    if not location_days:
        return None
    counts = {}
    for entry in location_days:
        name = entry.get("location")
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda name: (counts[name], name))


def _bedtime_shifted(bed_minutes):
    """A bedtime as minutes after the previous noon, so past-midnight times sort.

    Garmin publishes a bedtime as minutes of day, so 23:45 is 1425 but 00:30 is
    30 -- and a plain median over the two reads as an athlete who sleeps at
    noon. Shifting times before noon into the previous day's tail keeps the
    arithmetic honest for anyone who regularly crosses midnight.
    """
    if bed_minutes is None:
        return None
    value = float(bed_minutes)
    if value < 720:  # before noon: really the tail of the previous day
        value += 1440
    return value


def _format_clock(minutes):
    minutes = int(round(minutes)) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def night_contrasts(activities, sleep_history, hrv_history):
    """Groups of the athlete's own nights compared side by side.

    A correlation coefficient is the wrong lens for the questions worth asking
    here -- "what does a late bedtime cost tonight?", "what does a training day
    do to the following night?" -- because the honest reading is two group means
    and how far apart they sit, each with its night count travelling beside it.
    The contrasts and their thresholds are policy's (`NIGHT_CONTRASTS`); this
    function only sorts nights into those groups and measures them. A contrast
    whose thinner group is below `NIGHT_CONTRAST_MIN_NIGHTS` is refused, the
    same floor the correlation lab applies to paired days.

    A sleep record is labelled by its wake date, so "the night after a training
    day" groups by the day before the wake date, and an HRV reading joins the
    night it summarises. An evening session is one that *started* at or after
    `NIGHT_CONTRAST_EVENING_HOUR`, from the device's own start times.
    """
    sleeps = [r for r in sleep_history or [] if r.get("date")]
    if not sleeps:
        return None
    hrv = {r.get("date"): r.get("lastNightAvg") for r in hrv_history or [] if r.get("date")}

    sessions = {}
    for a in activities or []:
        start = str(a.get("startTimeLocal") or "")
        if len(start) < 13:
            continue
        day = start[:10]
        entry = sessions.setdefault(day, {"minutes": 0.0, "evening": False})
        entry["minutes"] += float(a.get("duration_min") or 0.0)
        try:
            if int(start[11:13]) >= policy.NIGHT_CONTRAST_EVENING_HOUR:
                entry["evening"] = True
        except ValueError:
            continue

    beds = [_bedtime_shifted(r.get("bedtime_minutes")) for r in sleeps]
    beds = [b for b in beds if b is not None]
    if not beds:
        return None
    beds.sort()
    middle = len(beds) // 2
    median_bed = (
        beds[middle] if len(beds) % 2 else (beds[middle - 1] + beds[middle]) / 2.0
    )

    def night_metric(record, metric):
        if metric == "hrv":
            return hrv.get(record.get("date"))
        if metric == "deep":
            seconds = record.get("deep_seconds")
            return None if seconds is None else seconds / 60.0
        return None

    # Days before the first wake date are outside the measured window: their
    # "previous day" is unknown, so nights can inherit from them. A day inside
    # the window with no logged session really was a rest day -- the device was
    # worn the next morning, so its absence is a measurement.
    window_start = min(date.fromisoformat(r["date"]) for r in sleeps)

    def sort_night(record, spec):
        """Which of the contrast's two groups this night belongs to, or None."""
        wake = date.fromisoformat(record.get("date"))
        previous_day = wake - timedelta(days=1)
        previous = sessions.get(previous_day.isoformat()) or {"minutes": 0.0, "evening": False}
        key = spec["key"]
        if key == "late_bedtime_hrv":
            bed = _bedtime_shifted(record.get("bedtime_minutes"))
            if bed is None:
                return None
            if bed - median_bed >= policy.NIGHT_CONTRAST_LATE_MINUTES:
                return "a"
            if abs(bed - median_bed) <= policy.NIGHT_CONTRAST_ONTIME_MINUTES:
                return "b"
            return None
        if key in ("after_training_hrv", "after_training_deep"):
            if previous_day < window_start:
                return None
            if previous.get("minutes", 0.0) >= policy.NIGHT_CONTRAST_TRAINING_MINUTES:
                return "a"
            return "b"
        if key == "evening_session_hrv":
            if previous_day < window_start:
                return None
            return "a" if previous.get("evening") else "b"
        return None

    published = []
    for spec in policy.NIGHT_CONTRASTS:
        groups = {"a": [], "b": []}
        for record in sleeps:
            side = sort_night(record, spec)
            if side is None:
                continue
            value = night_metric(record, spec["metric"])
            if value is not None:
                groups[side].append(value)
        if len(groups["a"]) < policy.NIGHT_CONTRAST_MIN_NIGHTS:
            continue
        if len(groups["b"]) < policy.NIGHT_CONTRAST_MIN_NIGHTS:
            continue
        mean_a = sum(groups["a"]) / len(groups["a"])
        mean_b = sum(groups["b"]) / len(groups["b"])
        published.append(
            {
                "key": spec["key"],
                "question": spec["question"],
                "metric_label": spec["metric_label"],
                "label_a": spec["label_a"],
                "label_b": spec["label_b"],
                "mean_a": round(mean_a, 1),
                "mean_b": round(mean_b, 1),
                "delta": round(mean_a - mean_b, 1),
                "nights_a": len(groups["a"]),
                "nights_b": len(groups["b"]),
                "evidence": policy.evidence(*spec["evidence"]),
            }
        )

    if not published:
        return None
    return {
        "median_bedtime": _format_clock(median_bed),
        "min_nights": policy.NIGHT_CONTRAST_MIN_NIGHTS,
        "late_minutes": policy.NIGHT_CONTRAST_LATE_MINUTES,
        "ontime_minutes": policy.NIGHT_CONTRAST_ONTIME_MINUTES,
        "training_minutes": policy.NIGHT_CONTRAST_TRAINING_MINUTES,
        "evening_hour": policy.NIGHT_CONTRAST_EVENING_HOUR,
        "caveat": policy.NIGHT_CONTRAST_CAVEAT,
        "contrasts": published,
    }
