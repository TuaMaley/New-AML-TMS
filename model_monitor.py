"""
AML-TMS Model Performance Monitor
====================================
Tracks model drift, performance degradation, and data distribution shifts.
Implements Population Stability Index (PSI), feature drift (KS test),
and rolling performance metrics.
"""
import math, random
from datetime import datetime, timedelta
from collections import defaultdict

random.seed(77)

# ── Population Stability Index ────────────────────────────────────────────────
def psi(expected: list, actual: list, buckets: int = 10) -> float:
    """
    Compute PSI between expected (training) and actual (production) distributions.
    PSI < 0.1: No significant change
    PSI 0.1–0.2: Moderate change — monitor closely
    PSI > 0.2: Significant change — retrain
    """
    if not expected or not actual:
        return 0.0

    mn = min(min(expected), min(actual))
    mx = max(max(expected), max(actual))
    if mx == mn:
        return 0.0

    bucket_size = (mx - mn) / buckets
    edges = [mn + i * bucket_size for i in range(buckets + 1)]

    def bucket_counts(data):
        counts = [0] * buckets
        for v in data:
            idx = min(int((v - mn) / bucket_size), buckets - 1)
            counts[idx] += 1
        return counts

    exp_counts = bucket_counts(expected)
    act_counts = bucket_counts(actual)
    n_exp = len(expected)
    n_act = len(actual)

    psi_val = 0.0
    for e, a in zip(exp_counts, act_counts):
        e_pct = max(e / n_exp, 0.0001)
        a_pct = max(a / n_act, 0.0001)
        psi_val += (a_pct - e_pct) * math.log(a_pct / e_pct)

    return round(psi_val, 4)


def ks_statistic(dist1: list, dist2: list) -> float:
    """
    Kolmogorov-Smirnov statistic between two distributions.
    KS > 0.1: Feature drift detected.
    """
    if not dist1 or not dist2:
        return 0.0

    all_vals = sorted(set(dist1 + dist2))
    n1, n2 = len(dist1), len(dist2)
    max_diff = 0.0

    for v in all_vals:
        cdf1 = sum(1 for x in dist1 if x <= v) / n1
        cdf2 = sum(1 for x in dist2 if x <= v) / n2
        max_diff = max(max_diff, abs(cdf1 - cdf2))

    return round(max_diff, 4)


# ── Drift Monitor ─────────────────────────────────────────────────────────────
class DriftMonitor:
    """
    Tracks feature distribution drift between training and production.
    Maintains a rolling window of production feature values.
    """
    def __init__(self, training_stats: dict, window_size: int = 1000):
        self.training_stats = training_stats  # {feature: {"mean": ..., "std": ..., "samples": [...]}}
        self.window_size = window_size
        self.production_buffer = defaultdict(list)
        self.drift_history = []
        self.alert_threshold = 0.10

    def record(self, features: dict):
        """Add a new production observation to the rolling buffer."""
        for feat, val in features.items():
            buf = self.production_buffer[feat]
            buf.append(float(val))
            if len(buf) > self.window_size:
                buf.pop(0)

    def compute_drift(self) -> dict:
        """Compute drift scores for all monitored features."""
        results = {}
        for feat, prod_vals in self.production_buffer.items():
            if len(prod_vals) < 50:
                continue
            train_info = self.training_stats.get(feat)
            if not train_info:
                continue
            train_samples = train_info.get("samples", [])
            if not train_samples:
                continue

            psi_val = psi(train_samples, prod_vals)
            ks_val  = ks_statistic(train_samples, prod_vals)

            results[feat] = {
                "psi":           psi_val,
                "ks":            ks_val,
                "drift_score":   round((psi_val + ks_val) / 2, 4),
                "alert":         psi_val > self.alert_threshold or ks_val > 0.15,
                "prod_mean":     round(sum(prod_vals) / len(prod_vals), 3),
                "prod_std":      round(_std(prod_vals), 3),
                "train_mean":    train_info.get("mean", 0),
                "train_std":     train_info.get("std", 0),
                "n_production":  len(prod_vals),
            }

        self.drift_history.append({
            "timestamp": datetime.now().isoformat(),
            "n_features_drifted": sum(1 for r in results.values() if r["alert"]),
            "max_psi": max((r["psi"] for r in results.values()), default=0),
            "max_ks":  max((r["ks"] for r in results.values()), default=0),
        })

        return results

    def needs_retrain(self) -> bool:
        if len(self.drift_history) < 3:
            return False
        recent = self.drift_history[-3:]
        return all(r["n_features_drifted"] > 2 for r in recent)


def _std(vals):
    if len(vals) < 2:
        return 0
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean)**2 for v in vals) / (len(vals) - 1))


# ── Rolling Performance Tracker ───────────────────────────────────────────────
class PerformanceTracker:
    """
    Tracks rolling precision, recall, and AUC using outcome labels
    fed back from SAR filing decisions.
    """
    def __init__(self, window: int = 500):
        self.window = window
        self.outcomes = []  # [{score, label, filed_as_sar, timestamp}]

    def record_outcome(self, score: int, filed_as_sar: bool):
        """Record the outcome (SAR/no-SAR) for a previously scored transaction."""
        self.outcomes.append({
            "score":        score,
            "label":        int(filed_as_sar),
            "timestamp":    datetime.now().isoformat(),
        })
        if len(self.outcomes) > self.window:
            self.outcomes.pop(0)

    def compute_metrics(self, threshold: int = 65) -> dict:
        if len(self.outcomes) < 20:
            return {"status": "insufficient_data", "n": len(self.outcomes)}

        preds = [int(o["score"] >= threshold) for o in self.outcomes]
        labels = [o["label"] for o in self.outcomes]

        tp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 1)
        fp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 0)
        tn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 0)
        fn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 1)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0
        fp_rate   = fp / (fp + tn) if (fp + tn) > 0 else 0

        return {
            "n": len(self.outcomes),
            "threshold": threshold,
            "precision": round(precision, 3),
            "recall":    round(recall, 3),
            "fp_rate":   round(fp_rate, 3),
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "alert_rate": round(sum(preds) / len(preds), 3),
            "sar_rate":   round(sum(labels) / len(labels), 3),
        }

    def get_score_distribution(self, bins: int = 10) -> list:
        """Score distribution of all tracked transactions."""
        if not self.outcomes:
            return []
        bucket_size = 100 // bins
        counts = [0] * bins
        for o in self.outcomes:
            idx = min(o["score"] // bucket_size, bins - 1)
            counts[idx] += 1
        return [{"bin": f"{i*bucket_size}–{(i+1)*bucket_size-1}", "count": c}
                for i, c in enumerate(counts)]


# ── Synthetic monitoring data (for demo) ─────────────────────────────────────
def generate_monitoring_report() -> dict:
    """Generate a synthetic monitoring report for the dashboard."""
    weeks = 12
    base_drift = 0.03

    drift_series = []
    for w in range(weeks):
        noise = random.uniform(-0.01, 0.015)
        spike = 0.04 if w == 5 else 0
        drift_series.append(round(base_drift + noise + spike, 4))

    perf_series = []
    base_auc = 0.94
    for w in range(weeks):
        noise = random.uniform(-0.005, 0.008)
        perf_series.append(round(base_auc + noise, 3))

    fp_series = []
    base_fp = 0.60
    for w in range(weeks):
        noise = random.uniform(-0.02, 0.02)
        fp_series.append(round(max(0.50, base_fp - w * 0.01 + noise), 3))

    top_drifted = [
        {"feature": "velocity_3d",     "psi": 0.04, "ks": 0.08, "trend": "↑"},
        {"feature": "amount",          "psi": 0.03, "ks": 0.06, "trend": "stable"},
        {"feature": "jurisdiction_idx","psi": 0.02, "ks": 0.05, "trend": "stable"},
        {"feature": "new_counterparty","psi": 0.05, "ks": 0.09, "trend": "↑"},
        {"feature": "cross_border",    "psi": 0.02, "ks": 0.04, "trend": "stable"},
    ]

    return {
        "weeks": [f"Week {i+1}" for i in range(weeks)],
        "drift_series":   drift_series,
        "perf_series":    perf_series,
        "fp_rate_series": fp_series,
        "retrain_threshold": 0.10,
        "current_drift": drift_series[-1],
        "current_auc":   perf_series[-1],
        "current_fp_rate": fp_series[-1],
        "top_drifted_features": top_drifted,
        "retrain_needed": drift_series[-1] > 0.10,
        "next_scheduled_retrain": (datetime.now() + timedelta(days=18)).strftime("%Y-%m-%d"),
        "last_retrain": datetime.now().strftime("%Y-%m-%d"),
        "total_scored_since_retrain": random.randint(180000, 250000),
        "outcome_labels_available": random.randint(800, 1200),
        "status": "Stable" if drift_series[-1] < 0.10 else "Drift detected — retrain recommended",
    }
