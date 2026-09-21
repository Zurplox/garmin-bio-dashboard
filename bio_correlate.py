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
