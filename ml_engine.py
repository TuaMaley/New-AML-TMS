"""
AML-TMS ML Engine
Trains 4 models on synthetic transaction data:
  1. Isolation Forest (unsupervised anomaly detection)
  2. XGBoost-style GradientBoosting (supervised classification)
  3. MLP Graph proxy (GNN-proxy via dense features)
  4. Gradient Boosting LSTM-proxy (temporal sequence model)
Exposes: score_transaction(), retrain(), get_metrics()
"""
import numpy as np
import pandas as pd
import json, time, math, random
from datetime import datetime, timedelta
from sklearn.ensemble import (
    IsolationForest, GradientBoostingClassifier, RandomForestClassifier
)
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

random.seed(42)
np.random.seed(42)

# ── Synthetic data generator ────────────────────────────────────────────────
CHANNELS = ["Wire Transfer", "Cash Deposit", "Fintech API",
            "FX/Treasury", "Trade Finance", "Mobile Banking"]
TYPOLOGIES = ["Structuring", "Layering", "Sanctions", "Crypto",
              "Trade AML", "Smurfing", "Mule Account", "Shell Co."]
JURISDICTIONS = ["Standard", "Elevated", "High", "OFAC-adjacent"]
TIERS = ["Low", "Medium", "High", "PEP"]

def synthetic_transaction(is_suspicious=False, seed=None):
    if seed: random.seed(seed)
    txn = {}
    # ── Original features ───────────────────────────────────────────────────
    txn["amount"] = (
        random.uniform(8000, 11000) if is_suspicious and random.random() < 0.4
        else random.uniform(500, 2_000_000)
    )
    txn["channel_idx"]       = random.randint(0, 5)
    txn["tier_idx"]          = random.randint(2, 3) if is_suspicious else random.randint(0, 3)
    txn["velocity_3d"]       = random.uniform(300, 900) if is_suspicious else random.uniform(-20, 150)
    txn["velocity_7d"]       = txn["velocity_3d"] * random.uniform(0.6, 1.4)
    txn["new_counterparty"]  = 1 if (is_suspicious and random.random()<0.7) else int(random.random()<0.1)
    txn["jurisdiction_idx"]  = random.randint(2, 3) if is_suspicious else random.randint(0, 3)
    txn["round_dollar"]      = 1 if (is_suspicious and random.random()<0.5) else int(random.random()<0.05)
    txn["hour_of_day"]       = random.choice([1,2,3,23]) if is_suspicious and random.random()<0.3 else random.randint(6,22)
    txn["counterparty_degree"]= random.randint(5, 40) if is_suspicious else random.randint(1, 15)
    txn["cross_border"]      = 1 if (is_suspicious and random.random()<0.8) else int(random.random()<0.15)
    txn["account_age_days"]  = random.randint(1, 60) if is_suspicious and random.random()<0.4 else random.randint(30, 3650)
    txn["prior_sars"]        = random.randint(0, 3) if is_suspicious else 0
    txn["amount_vs_peer_pct"]= random.uniform(200, 800) if is_suspicious else random.uniform(80, 120)
    txn["multi_currency"]    = 1 if (is_suspicious and random.random()<0.6) else int(random.random()<0.05)
    # ── New v2 features ──────────────────────────────────────────────────────
    txn["behavioral_drift"]  = random.uniform(0.45, 1.0) if is_suspicious else random.uniform(0.0, 0.35)
    txn["hist_fraud_rate"]   = random.uniform(0.04, 0.30) if is_suspicious else random.uniform(0.0, 0.02)
    txn["bene_risk_score"]   = random.uniform(60, 100)   if is_suspicious else random.uniform(5, 50)
    txn["dest_risk_score"]   = random.uniform(55, 100)   if is_suspicious else random.uniform(5, 45)
    txn["graph_centrality"]  = random.uniform(0.40, 0.99)if is_suspicious else random.uniform(0.01, 0.25)
    txn["bene_blacklist"]    = 1 if (is_suspicious and random.random()<0.20) else 0
    txn["failed_logins"]     = random.randint(2, 8)      if is_suspicious and random.random()<0.35 else random.randint(0, 1)
    txn["bene_reuse_count"]  = random.randint(6, 20)     if is_suspicious else random.randint(0, 5)
    txn["txn_sequence"]      = random.randint(1, 10)     if is_suspicious else random.randint(1, 50)
    txn["last_txn_gap"]      = random.randint(1, 120)    if is_suspicious else random.randint(60, 14400)
    txn["label"] = 1 if is_suspicious else 0
    return txn


def generate_dataset(n=5000):
    rows = []
    n_sus = int(n * 0.05)
    n_clean = n - n_sus
    for _ in range(n_sus):
        rows.append(synthetic_transaction(is_suspicious=True))
    for _ in range(n_clean):
        rows.append(synthetic_transaction(is_suspicious=False))
    df = pd.DataFrame(rows)
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)
    return df

FEATURE_COLS = [
    # ── Original core features ──────────────────────────────────────────────
    "amount",               # Raw transaction amount
    "channel_idx",          # Channel risk index (Wire=0 → Trade=5)
    "tier_idx",             # Customer risk tier (Low=0 → PEP=3)
    "velocity_3d",          # 3-day velocity % vs peer baseline
    "velocity_7d",          # 7-day velocity % vs peer baseline
    "new_counterparty",     # First-time counterparty flag
    "jurisdiction_idx",     # Jurisdiction risk (Standard=0 → OFAC=3)
    "round_dollar",         # Round-dollar structuring indicator
    "hour_of_day",          # Transaction hour (0-23)
    "counterparty_degree",  # Network degree (number of counterparties)
    "cross_border",         # Cross-border transaction flag
    "account_age_days",     # Account age in days
    "prior_sars",           # Prior SAR filings on record
    "amount_vs_peer_pct",   # Amount vs peer group median %
    "multi_currency",       # Multi-currency conversion flag
    # ── New features from v2 dataset ────────────────────────────────────────
    "behavioral_drift",     # Behavioural drift score (0-1)
    "hist_fraud_rate",      # Historical fraud rate for this entity
    "bene_risk_score",      # Beneficiary risk score (0-100)
    "dest_risk_score",      # Destination country risk score (0-100)
    "graph_centrality",     # Graph centrality (hub-node indicator)
    "bene_blacklist",       # Beneficiary blacklist/sanctions flag
    "failed_logins",        # Failed login count before this session
    "bene_reuse_count",     # How many times this beneficiary reused
    "txn_sequence",         # Position in entity's transaction sequence
    "last_txn_gap",         # Minutes since last transaction
]

SHAP_LABELS = {
    # Original
    "amount":               "Transaction amount",
    "channel_idx":          "Channel risk",
    "tier_idx":             "Customer risk tier",
    "velocity_3d":          "3-day velocity change",
    "velocity_7d":          "7-day velocity change",
    "new_counterparty":     "New counterparty flag",
    "jurisdiction_idx":     "Jurisdiction risk",
    "round_dollar":         "Round-dollar structuring",
    "hour_of_day":          "Transaction hour anomaly",
    "counterparty_degree":  "Counterparty network degree",
    "cross_border":         "Cross-border transaction",
    "account_age_days":     "Account age (days)",
    "prior_sars":           "Prior SAR history",
    "amount_vs_peer_pct":   "Amount vs peer group %",
    "multi_currency":       "Multi-currency conversion",
    # New
    "behavioral_drift":     "Behavioural drift score",
    "hist_fraud_rate":      "Historical fraud rate",
    "bene_risk_score":      "Beneficiary risk score",
    "dest_risk_score":      "Destination country risk",
    "graph_centrality":     "Graph centrality (hub indicator)",
    "bene_blacklist":       "Beneficiary blacklist flag",
    "failed_logins":        "Failed login count",
    "bene_reuse_count":     "Beneficiary reuse count",
    "txn_sequence":         "Transaction sequence position",
    "last_txn_gap":         "Time since last transaction (mins)",
}

# ── Model registry ──────────────────────────────────────────────────────────
class MLEngine:
    def __init__(self):
        self.scaler = StandardScaler()
        self.models = {}
        self.metrics = {}
        self.feature_importances = {}
        self.training_history = []
        self.trained_at = None
        self.train()

    def train(self):
        t0 = time.time()
        df = generate_dataset(6000)
        X = df[FEATURE_COLS].values
        y = df["label"].values

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        self.scaler.fit(X_train)
        Xtr = self.scaler.transform(X_train)
        Xte = self.scaler.transform(X_test)

        # 1. Isolation Forest (unsupervised)
        iso = IsolationForest(n_estimators=100, contamination=0.05,
                              random_state=42, n_jobs=-1)
        iso.fit(Xtr)
        iso_scores = -iso.score_samples(Xte)
        iso_norm = (iso_scores - iso_scores.min()) / (iso_scores.max() - iso_scores.min() + 1e-9)
        self.models["iso"] = iso
        self.metrics["iso"] = self._eval_binary(iso_norm, y_test, "iso")

        # 2. Gradient Boosting (XGBoost proxy)
        gb = GradientBoostingClassifier(n_estimators=150, max_depth=4,
                                         learning_rate=0.1, random_state=42)
        gb.fit(Xtr, y_train)
        self.models["xgb"] = gb
        self.metrics["xgb"] = self._eval(gb, Xte, y_test, "xgb")
        self.feature_importances["xgb"] = dict(zip(FEATURE_COLS, gb.feature_importances_))

        # 3. MLP (GNN proxy — learns graph-like interactions)
        mlp = MLPClassifier(hidden_layer_sizes=(128, 64, 32), max_iter=300,
                             random_state=42, early_stopping=True)
        mlp.fit(Xtr, y_train)
        self.models["gnn"] = mlp
        self.metrics["gnn"] = self._eval(mlp, Xte, y_test, "gnn")

        # 4. Random Forest (LSTM temporal proxy)
        rf = RandomForestClassifier(n_estimators=120, max_depth=8,
                                    random_state=42, n_jobs=-1)
        rf.fit(Xtr, y_train)
        self.models["lstm"] = rf
        self.metrics["lstm"] = self._eval(rf, Xte, y_test, "lstm")

        elapsed = round(time.time() - t0, 2)
        self.trained_at = datetime.now().isoformat()
        self.training_history.append({
            "timestamp": self.trained_at,
            "duration_s": elapsed,
            "n_samples": len(df),
            "metrics": {k: v["auc"] for k, v in self.metrics.items()}
        })
        print(f"[MLEngine] Training complete in {elapsed}s")
        for k, v in self.metrics.items():
            print(f"  {k}: AUC={v['auc']:.3f} P={v['precision']:.3f} R={v['recall']:.3f}")

    def _eval(self, model, X, y, name):
        proba = model.predict_proba(X)[:, 1]
        pred = (proba >= 0.5).astype(int)
        return {
            "auc": round(roc_auc_score(y, proba), 3),
            "precision": round(precision_score(y, pred, zero_division=0), 3),
            "recall": round(recall_score(y, pred, zero_division=0), 3),
        }

    def _eval_binary(self, scores, y, name):
        threshold = np.percentile(scores, 80)
        pred = (scores >= threshold).astype(int)
        try:
            auc = round(roc_auc_score(y, scores), 3)
        except:
            auc = 0.5
        return {
            "auc": auc,
            "precision": round(precision_score(y, pred, zero_division=0), 3),
            "recall": round(recall_score(y, pred, zero_division=0), 3),
        }

    def score_transaction(self, txn_dict: dict) -> dict:
        """Score a single transaction dict. Returns full ensemble result."""
        row = [txn_dict.get(f, 0) for f in FEATURE_COLS]
        X = np.array(row).reshape(1, -1)
        Xs = self.scaler.transform(X)

        # Individual model scores (0-100)
        iso_raw = -self.models["iso"].score_samples(Xs)[0]
        # Normalise iso using rough known range
        iso_score = int(min(100, max(0, (iso_raw + 0.3) * 100)))

        xgb_prob = self.models["xgb"].predict_proba(Xs)[0][1]
        gnn_prob = self.models["gnn"].predict_proba(Xs)[0][1]
        lstm_prob = self.models["lstm"].predict_proba(Xs)[0][1]

        xgb_score = int(round(xgb_prob * 100))
        gnn_score = int(round(gnn_prob * 100))
        lstm_score = int(round(lstm_prob * 100))

        # Weighted ensemble (XGB highest weight, tuned from validation)
        ensemble_prob = (
            0.15 * (iso_score / 100) +
            0.40 * xgb_prob +
            0.25 * gnn_prob +
            0.20 * lstm_prob
        )
        raw_score = int(round(ensemble_prob * 100))

        # FP suppression: if all 4 models are below medium, suppress
        votes_high = sum([iso_score > 55, xgb_score > 55, gnn_score > 55, lstm_score > 55])
        suppressed = votes_high == 0 and raw_score < 45
        final_score = 0 if suppressed else raw_score

        # Priority
        if final_score >= 85: priority = "critical"
        elif final_score >= 70: priority = "high"
        elif final_score >= 55: priority = "medium"
        else: priority = "low"

        action = "GENERATE ALERT" if final_score >= 65 else "SUPPRESS / LOG ONLY"

        # SHAP approximation via feature importances + direction
        shap_values = self._approx_shap(txn_dict, xgb_prob)

        # Typology prediction
        typology = self._predict_typology(txn_dict, final_score)

        return {
            "score": final_score,
            "priority": priority,
            "action": action,
            "suppressed": suppressed,
            "model_scores": {
                "iso": iso_score,
                "xgb": xgb_score,
                "gnn": gnn_score,
                "lstm": lstm_score,
            },
            "ensemble_weights": {"iso": 0.15, "xgb": 0.40, "gnn": 0.25, "lstm": 0.20},
            "shap": shap_values,
            "typology": typology,
            "confidence": round(abs(ensemble_prob - 0.5) * 2, 3),
            "scored_at": datetime.now().isoformat(),
        }

    def _approx_shap(self, txn, xgb_prob):
        """Approximate SHAP using feature importance × deviation from mean."""
        imp = self.feature_importances.get("xgb", {})
        baselines = {
            "amount": 50000, "channel_idx": 2, "tier_idx": 1,
            "velocity_3d": 30, "velocity_7d": 40, "new_counterparty": 0.1,
            "jurisdiction_idx": 0.8, "round_dollar": 0.05, "hour_of_day": 13,
            "counterparty_degree": 5, "cross_border": 0.15,
            "account_age_days": 500, "prior_sars": 0,
            "amount_vs_peer_pct": 100, "multi_currency": 0.05,
        }
        # Features that increase risk when above baseline
        positive_dir = {
            "amount", "velocity_3d", "velocity_7d", "new_counterparty",
            "jurisdiction_idx", "tier_idx", "round_dollar",
            "counterparty_degree", "cross_border", "prior_sars",
            "amount_vs_peer_pct", "multi_currency"
        }
        results = []
        for feat in FEATURE_COLS:
            val = txn.get(feat, baselines.get(feat, 0))
            base = baselines.get(feat, 0)
            feat_imp = imp.get(feat, 0.05)
            norm_base = base if base != 0 else 1
            deviation = (val - base) / (abs(norm_base) + 1e-6)
            direction = 1 if feat in positive_dir else -1
            shap_val = round(feat_imp * deviation * direction * (xgb_prob + 0.1), 3)
            shap_val = max(-0.5, min(0.5, shap_val))
            results.append({
                "feature": feat,
                "label": SHAP_LABELS.get(feat, feat),
                "value": round(float(val), 2),
                "shap": shap_val,
            })
        results.sort(key=lambda x: abs(x["shap"]), reverse=True)
        return results[:8]

    def _predict_typology(self, txn, score):
        if score < 50: return "No typology detected"
        v = txn.get("velocity_3d", 0)
        rd = txn.get("round_dollar", 0)
        cb = txn.get("cross_border", 0)
        mc = txn.get("multi_currency", 0)
        jur = txn.get("jurisdiction_idx", 0)
        amt = txn.get("amount", 0)
        if rd and v > 100: return "Structuring / threshold evasion"
        if cb and mc and jur >= 2: return "Cross-border layering"
        if amt < 11000 and rd: return "Smurfing pattern"
        if jur >= 3: return "Sanctions-adjacent activity"
        if mc: return "FX structuring"
        return "Anomalous transaction pattern"

    def get_metrics(self):
        return self.metrics

    def get_feature_importances(self):
        return self.feature_importances.get("xgb", {})

    def get_training_history(self):
        return self.training_history

    def score_batch(self, txns):
        return [self.score_transaction(t) for t in txns]


# Singleton
_engine = None

def get_engine():
    global _engine
    if _engine is not None:
        return _engine

    import os as _os, joblib as _jl
    _cache = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'ml_model_cache.pkl')

    # Try loading cached models — saves ~5-10s on cold starts
    if _os.path.exists(_cache):
        try:
            _cached = _jl.load(_cache)
            _engine = MLEngine.__new__(MLEngine)
            _engine.models  = _cached['models']
            _engine.scaler  = _cached['scaler']
            _engine.metrics = _cached.get('metrics', {})
            _engine.trained = True
            print("[MLEngine] Loaded from cache (skipping training)", flush=True)
            return _engine
        except Exception as _e:
            print(f"[MLEngine] Cache load failed ({_e}), retraining...", flush=True)

    # No cache — train fresh and save for next startup
    _engine = MLEngine()
    try:
        _jl.dump({'models': _engine.models, 'scaler': _engine.scaler,
                  'metrics': _engine.metrics}, _cache, compress=3)
        print(f"[MLEngine] Model cache saved ({_os.path.getsize(_cache)//1024} KB)", flush=True)
    except Exception as _e:
        print(f"[MLEngine] Cache save failed: {_e}", flush=True)
    return _engine
