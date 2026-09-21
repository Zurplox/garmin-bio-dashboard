"""
Provenance: who supplied each number, and whether the run may be published.

Every Garmin endpoint can fail independently, and a failed call used to be
swallowed while a hard-coded placeholder was substituted. The dashboard then
rendered that placeholder as a measurement, so "ACWR 0.2", "47 steps" and
"+38 pts charged" were indistinguishable from real readings.

Fetching layers record the origin of each metric group here; the orchestrator
reads the summary to decide whether the dataset is fit to publish.
"""

from bio_policy import CORE_METRICS, METRIC_LABELS


class DataQuality:
    """Records the origin of each metric group: live Garmin data or a fallback."""

    def __init__(self):
        self.metrics = {}

    def record(self, key, live, note=""):
        self.metrics[key] = {
            "key": key,
            "label": METRIC_LABELS.get(key, key),
            "source": "live" if live else "fallback",
            "note": note,
        }

    def core_failures(self):
        """Labels of core metrics that fell back, i.e. why a run must not publish."""
        return [
            METRIC_LABELS.get(key, key)
            for key in CORE_METRICS
            if self.metrics.get(key, {}).get("source") != "live"
        ]

    def publishable(self):
        return not self.core_failures()

    def as_dict(self):
        degraded = [m["label"] for m in self.metrics.values() if m["source"] != "live"]
        total = len(self.metrics)
        live_count = total - len(degraded)
        return {
            "metrics": self.metrics,
            "degraded": degraded,
            "live_count": live_count,
            "total_count": total,
            "live_pct": round(live_count / total * 100) if total else 0,
            "core_degraded": self.core_failures(),
        }
