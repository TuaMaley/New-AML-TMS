"""
AML-TMS Feature Engineering Module
====================================
Computes all 214 features across 5 categories for the ML pipeline.

Categories:
  1. Velocity & Volume (52 features)
  2. Network / Graph (48 features)
  3. Behavioural Baseline (44 features)
  4. Typology Indicators (42 features)
  5. Entity Risk Context (28 features)
"""
import numpy as np
import math
from datetime import datetime

# ── Category definitions ─────────────────────────────────────────────────────
FEATURE_REGISTRY = {}

def feature(category, name, description):
    """Decorator to register a feature function."""
    def decorator(fn):
        FEATURE_REGISTRY[name] = {
            "fn": fn, "category": category, "description": description
        }
        return fn
    return decorator

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 1 — VELOCITY & VOLUME (52 features)
# ─────────────────────────────────────────────────────────────────────────────

def compute_velocity_features(txn: dict, history: list) -> dict:
    """
    Compute velocity and volume features from transaction history.
    history: list of prior transactions [{amount, timestamp, channel, ...}]
    """
    amount = float(txn.get("amount", 0))
    now = txn.get("timestamp", datetime.now())

    # Rolling window amounts
    windows = {"1d": 1, "3d": 3, "7d": 7, "14d": 14, "30d": 30, "90d": 90}
    window_amounts = {}
    window_counts = {}
    window_max = {}

    for label, days in windows.items():
        relevant = [h["amount"] for h in history
                    if _days_ago(h.get("timestamp", now), now) <= days]
        window_amounts[label] = sum(relevant)
        window_counts[label] = len(relevant)
        window_max[label] = max(relevant) if relevant else 0

    # Baseline (90d average daily)
    baseline_daily = window_amounts["90d"] / 90 if window_amounts["90d"] else 1

    features = {
        # Absolute amounts in windows
        "vel_amount_1d":  round(window_amounts["1d"], 2),
        "vel_amount_3d":  round(window_amounts["3d"], 2),
        "vel_amount_7d":  round(window_amounts["7d"], 2),
        "vel_amount_14d": round(window_amounts["14d"], 2),
        "vel_amount_30d": round(window_amounts["30d"], 2),
        "vel_amount_90d": round(window_amounts["90d"], 2),

        # Transaction counts in windows
        "vel_count_1d":  window_counts["1d"],
        "vel_count_3d":  window_counts["3d"],
        "vel_count_7d":  window_counts["7d"],
        "vel_count_14d": window_counts["14d"],
        "vel_count_30d": window_counts["30d"],
        "vel_count_90d": window_counts["90d"],

        # Change ratios vs baseline
        "vel_ratio_1d_vs_90d":  round(_safe_ratio(window_amounts["1d"],  baseline_daily), 3),
        "vel_ratio_3d_vs_90d":  round(_safe_ratio(window_amounts["3d"],  baseline_daily * 3), 3),
        "vel_ratio_7d_vs_90d":  round(_safe_ratio(window_amounts["7d"],  baseline_daily * 7), 3),
        "vel_ratio_30d_vs_90d": round(_safe_ratio(window_amounts["30d"], baseline_daily * 30), 3),

        # This transaction vs window averages
        "vel_txn_vs_1d_avg":  round(_safe_ratio(amount, window_amounts["1d"]  / max(window_counts["1d"],  1)), 3),
        "vel_txn_vs_7d_avg":  round(_safe_ratio(amount, window_amounts["7d"]  / max(window_counts["7d"],  1)), 3),
        "vel_txn_vs_30d_avg": round(_safe_ratio(amount, window_amounts["30d"] / max(window_counts["30d"], 1)), 3),
        "vel_txn_vs_90d_avg": round(_safe_ratio(amount, window_amounts["90d"] / max(window_counts["90d"], 1)), 3),

        # Max single transaction in window
        "vel_max_1d":  round(window_max["1d"], 2),
        "vel_max_7d":  round(window_max["7d"], 2),
        "vel_max_30d": round(window_max["30d"], 2),

        # Current txn as pct of period max
        "vel_pct_of_7d_max":  round(_safe_ratio(amount, window_max["7d"]  or 1) * 100, 2),
        "vel_pct_of_30d_max": round(_safe_ratio(amount, window_max["30d"] or 1) * 100, 2),

        # Step-up detection (3d vs prior 3d)
        "vel_step_up_3d":  _step_up(history, 3, 3, now),
        "vel_step_up_7d":  _step_up(history, 7, 7, now),
        "vel_step_up_30d": _step_up(history, 30, 30, now),

        # Burst detection (many txns in short window)
        "vel_burst_1h":  _count_within_hours(history, now, 1),
        "vel_burst_4h":  _count_within_hours(history, now, 4),
        "vel_burst_24h": _count_within_hours(history, now, 24),

        # Cumulative daily amounts (structuring detection)
        "vel_daily_sum_today": round(_daily_sum(history, now, 0), 2),
        "vel_daily_sum_yesterday": round(_daily_sum(history, now, 1), 2),

        # Amount distribution
        "vel_amount_cv":    round(_coeff_variation([h["amount"] for h in history[-30:]]), 3),
        "vel_amount_skew":  round(_skewness([h["amount"] for h in history[-30:]]), 3),
        "vel_amount_kurtosis": round(_kurtosis([h["amount"] for h in history[-30:]]), 3),

        # Amount thresholds (structuring signals)
        "vel_near_10k_count": len([h for h in history if 9000 <= h["amount"] < 10000]),
        "vel_near_3k_count":  len([h for h in history if 2800 <= h["amount"] < 3000]),
        "vel_round_dollar_pct": round(_round_dollar_pct(history), 3),

        # Interarrival time features
        "vel_avg_interarrival_h": round(_avg_interarrival(history, now), 2),
        "vel_min_interarrival_h": round(_min_interarrival(history, now), 2),

        # Weekend / off-hours concentration
        "vel_weekend_pct":    round(_weekend_pct(history), 3),
        "vel_offhours_pct":   round(_offhours_pct(history), 3),
        "vel_night_count_7d": _night_count(history, now, 7),

        # Dormancy break
        "vel_days_since_last": round(_days_ago(history[-1]["timestamp"], now) if history else 999, 1),
        "vel_dormancy_break":  int(_days_ago(history[-1]["timestamp"] if history else now, now) > 90),

        # High-value concentration
        "vel_pct_above_50k_30d": round(_pct_above(history, 50000, 30, now), 3),
        "vel_pct_above_10k_30d": round(_pct_above(history, 10000, 30, now), 3),

        # Net flow direction
        "vel_net_flow_7d":  round(_net_flow(history, 7, now), 2),
        "vel_net_flow_30d": round(_net_flow(history, 30, now), 2),

        # Current txn
        "vel_current_amount": round(amount, 2),
        "vel_current_vs_median_90d": round(_safe_ratio(amount, _median([h["amount"] for h in history[-90:]]) or 1), 3),
    }
    return features  # 52 features


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 2 — NETWORK / GRAPH (48 features)
# ─────────────────────────────────────────────────────────────────────────────

def compute_network_features(txn: dict, entity_graph: dict) -> dict:
    """
    Compute network / graph features for an entity.
    entity_graph: {
        counterparties: [{id, amount, count, risk_score, jurisdiction}],
        community_id: int,
        hub_score: float,
        ...
    }
    """
    cps = entity_graph.get("counterparties", [])
    cp_count = len(cps)
    cp_amounts = [c.get("amount", 0) for c in cps]
    cp_risk = [c.get("risk_score", 0) for c in cps]
    cp_jur = [c.get("jurisdiction_idx", 0) for c in cps]
    new_cps = [c for c in cps if c.get("days_known", 999) < 7]
    high_risk_cps = [c for c in cps if c.get("risk_score", 0) > 70]

    features = {
        # Degree centrality
        "net_degree":           cp_count,
        "net_degree_new_7d":    len(new_cps),
        "net_degree_new_30d":   len([c for c in cps if c.get("days_known", 999) < 30]),
        "net_degree_high_risk": len(high_risk_cps),

        # Amount flow features
        "net_total_flow_in":    round(sum(c.get("amount", 0) for c in cps if c.get("direction") == "in"), 2),
        "net_total_flow_out":   round(sum(c.get("amount", 0) for c in cps if c.get("direction") == "out"), 2),
        "net_max_single_cp":    round(max(cp_amounts) if cp_amounts else 0, 2),
        "net_avg_cp_amount":    round(sum(cp_amounts) / max(cp_count, 1), 2),
        "net_cp_amount_cv":     round(_coeff_variation(cp_amounts), 3),

        # Concentration (Herfindahl index)
        "net_herfindahl":       round(_herfindahl(cp_amounts), 4),
        "net_top1_pct":         round(_safe_ratio(max(cp_amounts) if cp_amounts else 0, sum(cp_amounts) or 1), 3),
        "net_top3_pct":         round(_safe_ratio(sum(sorted(cp_amounts, reverse=True)[:3]), sum(cp_amounts) or 1), 3),

        # Risk of counterparty network
        "net_cp_avg_risk":      round(sum(cp_risk) / max(len(cp_risk), 1), 2),
        "net_cp_max_risk":      round(max(cp_risk) if cp_risk else 0, 2),
        "net_cp_pct_high_risk": round(len(high_risk_cps) / max(cp_count, 1), 3),
        "net_cp_risk_weighted_amt": round(sum(cp_amounts[i] * cp_risk[i] / 100 for i in range(len(cp_amounts))), 2) if cp_amounts else 0,

        # Jurisdiction spread
        "net_jur_unique":       len(set(cp_jur)),
        "net_jur_high_risk_pct": round(len([j for j in cp_jur if j >= 2]) / max(len(cp_jur), 1), 3),
        "net_jur_ofac_pct":     round(len([j for j in cp_jur if j >= 3]) / max(len(cp_jur), 1), 3),

        # Graph topology
        "net_hub_score":        round(entity_graph.get("hub_score", 0), 4),
        "net_community_size":   entity_graph.get("community_size", 1),
        "net_community_risk":   round(entity_graph.get("community_risk", 0), 3),
        "net_betweenness":      round(entity_graph.get("betweenness", 0), 4),
        "net_clustering_coef":  round(entity_graph.get("clustering", 0), 4),
        "net_is_hub":           int(entity_graph.get("hub_score", 0) > 0.7),
        "net_is_bridge":        int(entity_graph.get("betweenness", 0) > 0.5),

        # Second-degree network
        "net_2hop_degree":      entity_graph.get("two_hop_degree", 0),
        "net_2hop_high_risk":   entity_graph.get("two_hop_high_risk", 0),
        "net_2hop_sar_entities": entity_graph.get("two_hop_sar_entities", 0),

        # Transaction patterns with counterparties
        "net_cp_rapid_sequence": int(len(new_cps) >= 3),
        "net_cp_fan_out":        int(cp_count > 10 and entity_graph.get("hub_score", 0) > 0.5),
        "net_round_trip_flag":   int(entity_graph.get("round_trip_detected", False)),
        "net_shell_indicators":  int(entity_graph.get("shell_score", 0) > 0.6),

        # SAR network proximity
        "net_cp_with_sars":     entity_graph.get("cp_with_prior_sars", 0),
        "net_sar_proximity_1hop": int(entity_graph.get("cp_with_prior_sars", 0) > 0),
        "net_sar_proximity_2hop": int(entity_graph.get("two_hop_sar_entities", 0) > 0),

        # Cross-institution signals
        "net_cross_institution": int(entity_graph.get("cross_institution_links", 0) > 2),
        "net_multi_product":     int(entity_graph.get("product_count", 1) > 2),

        # Geographic dispersion
        "net_geo_spread":        entity_graph.get("geo_spread_index", 0),
        "net_cross_border_pct":  round(entity_graph.get("cross_border_pct", 0), 3),
        "net_offshore_pct":      round(entity_graph.get("offshore_pct", 0), 3),

        # Temporal network patterns
        "net_cp_churn_rate":     round(entity_graph.get("cp_churn_rate", 0), 3),
        "net_new_cp_pct_30d":    round(len([c for c in cps if c.get("days_known", 999) < 30]) / max(cp_count, 1), 3),

        # Entity age in network
        "net_entity_age_days":   entity_graph.get("entity_age_days", 365),
        "net_first_seen_days":   entity_graph.get("days_since_first_txn", 365),
    }
    return features  # 48 features


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 3 — BEHAVIOURAL BASELINE (44 features)
# ─────────────────────────────────────────────────────────────────────────────

def compute_behavioural_features(txn: dict, customer_profile: dict, peer_group_stats: dict) -> dict:
    """
    Computes deviation from established customer baseline and peer group.
    customer_profile: historical averages for this customer
    peer_group_stats: mean/std for this customer's risk-segment peer group
    """
    amount = float(txn.get("amount", 0))
    channel = txn.get("channel_idx", 0)
    hour = txn.get("hour_of_day", 12)
    cross_border = int(txn.get("cross_border", 0))
    multi_currency = int(txn.get("multi_currency", 0))

    cust_avg = float(customer_profile.get("avg_amount", 10000))
    cust_std = float(customer_profile.get("std_amount", 5000)) or 1
    cust_median = float(customer_profile.get("median_amount", 8000))
    cust_typical_hour = float(customer_profile.get("typical_hour", 12))
    cust_typical_channel = int(customer_profile.get("typical_channel", 0))

    peer_avg = float(peer_group_stats.get("mean_amount", 15000))
    peer_std = float(peer_group_stats.get("std_amount", 8000)) or 1
    peer_cb_rate = float(peer_group_stats.get("cross_border_rate", 0.1))

    features = {
        # Amount vs own baseline
        "beh_amount_z_score":       round((amount - cust_avg) / cust_std, 3),
        "beh_amount_vs_median":     round(_safe_ratio(amount, cust_median), 3),
        "beh_amount_pct_of_avg":    round(_safe_ratio(amount, cust_avg) * 100, 2),
        "beh_amount_std_above":     round(max(0, (amount - cust_avg) / cust_std), 3),
        "beh_is_3std_outlier":      int(abs(amount - cust_avg) > 3 * cust_std),
        "beh_is_5std_outlier":      int(abs(amount - cust_avg) > 5 * cust_std),

        # Amount vs peer group
        "beh_peer_z_score":         round((amount - peer_avg) / peer_std, 3),
        "beh_peer_pct":             round(_safe_ratio(amount, peer_avg) * 100, 2),
        "beh_peer_rank_pct":        round(customer_profile.get("peer_rank_pct", 50), 2),

        # Channel deviation
        "beh_channel_typical":      int(channel == cust_typical_channel),
        "beh_channel_new":          int(channel != cust_typical_channel and customer_profile.get("channel_variety", 1) < 2),
        "beh_channel_count_90d":    int(customer_profile.get("channel_variety", 1)),
        "beh_channel_switch":       int(customer_profile.get("recent_channel_switch", False)),

        # Temporal deviation
        "beh_hour_deviation":       round(min(abs(hour - cust_typical_hour), 24 - abs(hour - cust_typical_hour)), 2),
        "beh_offhours_flag":        int(hour < 6 or hour > 22),
        "beh_weekend_flag":         int(txn.get("day_of_week", 1) >= 5),
        "beh_unusual_timing":       int(abs(hour - cust_typical_hour) > 6),

        # Cross-border behaviour
        "beh_cb_rate_vs_peer":      round(_safe_ratio(cross_border, peer_cb_rate + 0.01), 3),
        "beh_cb_new_flag":          int(cross_border and customer_profile.get("historical_cb_rate", 0) < 0.05),
        "beh_mc_new_flag":          int(multi_currency and customer_profile.get("historical_mc_rate", 0) < 0.05),
        "beh_cb_frequency_change":  round(customer_profile.get("cb_frequency_change", 0), 3),

        # Account behaviour change signals
        "beh_activation_spike":     int(customer_profile.get("recent_activity_ratio", 1) > 5),
        "beh_pattern_break_flag":   int(customer_profile.get("pattern_break_score", 0) > 0.7),
        "beh_dormancy_reactivation":int(customer_profile.get("dormant_reactivated", False)),
        "beh_new_product_flag":     int(customer_profile.get("new_product_days", 999) < 30),

        # Counterparty behaviour
        "beh_new_cp_rate_30d":      round(customer_profile.get("new_cp_rate_30d", 0), 3),
        "beh_cp_churn_flag":        int(customer_profile.get("cp_churn_rate", 0) > 0.5),
        "beh_cp_concentration_change": round(customer_profile.get("cp_concentration_change", 0), 3),

        # Peer comparison percentiles
        "beh_velocity_peer_pct":    round(customer_profile.get("velocity_peer_pct", 50), 2),
        "beh_frequency_peer_pct":   round(customer_profile.get("frequency_peer_pct", 50), 2),
        "beh_amount_peer_pct":      round(customer_profile.get("amount_peer_pct", 50), 2),

        # Lifestyle consistency
        "beh_income_consistent":    int(customer_profile.get("income_consistent", True)),
        "beh_spending_consistent":  int(customer_profile.get("spending_consistent", True)),
        "beh_occupation_match":     int(customer_profile.get("occupation_match", True)),

        # Historical risk trajectory
        "beh_risk_score_trend":     round(customer_profile.get("risk_score_trend", 0), 3),
        "beh_alert_frequency_90d":  int(customer_profile.get("alert_count_90d", 0)),
        "beh_prior_fp_rate":        round(customer_profile.get("prior_fp_rate", 0), 3),
        "beh_prior_sar_rate":       round(customer_profile.get("prior_sar_rate", 0), 3),

        # Composite behavioural anomaly score
        "beh_composite_anomaly":    round(customer_profile.get("composite_anomaly_score", 0), 3),
        "beh_baseline_confidence":  round(customer_profile.get("baseline_confidence", 0.5), 3),
        "beh_months_of_history":    int(customer_profile.get("months_of_history", 0)),
        "beh_account_age_days":     int(customer_profile.get("account_age_days", 365)),

        # Recency
        "beh_days_since_last_alert": int(customer_profile.get("days_since_last_alert", 999)),
        "beh_alert_count_30d":      int(customer_profile.get("alert_count_30d", 0)),
    }
    return features  # 44 features


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 4 — TYPOLOGY INDICATORS (42 features)
# ─────────────────────────────────────────────────────────────────────────────

def compute_typology_features(txn: dict, history: list) -> dict:
    """
    Direct signal features for specific AML typology detection.
    Maps to FinCEN's 8 National Priority areas.
    """
    amount = float(txn.get("amount", 0))
    now = txn.get("timestamp", datetime.now())

    # Structuring indicators
    recent = [h["amount"] for h in history if _days_ago(h.get("timestamp", now), now) <= 10]
    near_threshold = [a for a in recent if 9000 <= a < 10000]
    near_3k = [a for a in recent if 2800 <= a < 3000]
    just_below = [a for a in recent if 9500 <= a < 10000]

    # Trade-based indicators
    invoice_match = float(txn.get("invoice_amount_match", 1.0))
    trade_goods_risk = int(txn.get("trade_goods_risk", 0))
    overunder_value = float(txn.get("over_under_value_flag", 0))

    features = {
        # Structuring / smurfing signals
        "typ_near_10k_count_10d":       len(near_threshold),
        "typ_near_10k_flag":            int(9000 <= amount < 10000),
        "typ_just_below_flag":          int(9500 <= amount < 10000),
        "typ_just_below_count_10d":     len(just_below),
        "typ_near_3k_count":            len(near_3k),
        "typ_round_dollar_flag":        int(amount % 1000 == 0),
        "typ_round_500_flag":           int(amount % 500 == 0),
        "typ_structuring_score":        round(min(1.0, len(near_threshold) / 5), 3),
        "typ_smurfing_split_count":     len([a for a in recent if a < 3000]),

        # Layering signals
        "typ_rapid_move_flag":          int(txn.get("velocity_3d", 0) > 500),
        "typ_multi_hop_flag":           int(txn.get("counterparty_degree", 0) > 10),
        "typ_round_trip_flag":          int(txn.get("net_round_trip_flag", False)),
        "typ_shell_score":              round(txn.get("net_shell_indicators", 0), 3),
        "typ_layering_score":           round(min(1.0, txn.get("counterparty_degree", 0) / 15), 3),
        "typ_integration_flag":         int(txn.get("new_counterparty", 0) == 0 and txn.get("velocity_3d", 0) < 50),

        # Sanctions / proliferation financing
        "typ_ofac_jurisdiction":        int(txn.get("jurisdiction_idx", 0) >= 3),
        "typ_pep_involved":             int(txn.get("tier_idx", 0) >= 3),
        "typ_sanctions_proximity":      round(txn.get("net_sar_proximity_1hop", 0), 3),
        "typ_dual_use_goods":           int(trade_goods_risk),
        "typ_proliferation_corridor":   int(txn.get("jurisdiction_idx", 0) >= 2 and txn.get("cross_border", 0)),

        # Fraud / cybercrime signals
        "typ_new_device_flag":          int(txn.get("new_device", False)),
        "typ_account_takeover_score":   round(txn.get("ato_score", 0), 3),
        "typ_unusual_access_flag":      int(txn.get("offhours_flag", False)),
        "typ_rapid_external_transfer":  int(txn.get("cross_border", 0) and txn.get("velocity_3d", 0) > 300),
        "typ_credential_change_flag":   int(txn.get("recent_credential_change", False)),

        # Trade-based AML
        "typ_invoice_mismatch":         round(abs(1 - invoice_match), 3),
        "typ_overvalue_flag":           int(overunder_value > 0.3),
        "typ_undervalue_flag":          int(overunder_value < -0.3),
        "typ_trade_goods_risk":         int(trade_goods_risk),
        "typ_multiple_invoices":        int(txn.get("multiple_invoices_flag", False)),
        "typ_phantom_shipment":         int(txn.get("phantom_shipment_flag", False)),

        # Crypto / virtual asset
        "typ_virtual_asset_flag":       int(txn.get("channel_idx", 0) == 2 and txn.get("multi_currency", 0)),
        "typ_crypto_conversion":        int(txn.get("multi_currency", 0) and txn.get("cross_border", 0)),
        "typ_mixer_proximity":          int(txn.get("mixer_flag", False)),
        "typ_defi_flag":               int(txn.get("defi_flag", False)),

        # Terrorist financing
        "typ_high_risk_corridor_flag":  int(txn.get("jurisdiction_idx", 0) >= 2),
        "typ_charity_anomaly":          int(txn.get("charity_flag", False)),
        "typ_small_frequent_flag":      int(len([a for a in recent if a < 500]) > 5),

        # Human trafficking / elder fraud
        "typ_elder_financial_flag":     int(txn.get("elder_flag", False)),
        "typ_wage_payment_anomaly":     int(txn.get("wage_anomaly_flag", False)),
        "typ_cash_intensive_flag":      int(txn.get("channel_idx", 0) == 1),

        # Composite typology likelihood
        "typ_primary_typology_score":   round(txn.get("primary_typology_likelihood", 0), 3),
        "typ_multi_typology_flag":      int(txn.get("multi_typology_indicators", 0) >= 2),
    }
    return features  # 42 features


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 5 — ENTITY RISK CONTEXT (28 features)
# ─────────────────────────────────────────────────────────────────────────────

def compute_entity_risk_features(txn: dict, entity_profile: dict) -> dict:
    """
    Static + dynamic entity risk context features.
    entity_profile: KYC data, risk ratings, CDD results.
    """
    features = {
        # Customer risk rating (CDD/KYC output)
        "ent_risk_tier":            int(entity_profile.get("risk_tier_idx", 1)),
        "ent_risk_score_kyc":       round(entity_profile.get("kyc_risk_score", 0), 2),
        "ent_cdd_refresh_days":     int(entity_profile.get("days_since_cdd_refresh", 365)),
        "ent_cdd_overdue":          int(entity_profile.get("days_since_cdd_refresh", 365) > 365),
        "ent_kyc_completeness":     round(entity_profile.get("kyc_completeness", 1.0), 3),

        # PEP & sanctions
        "ent_is_pep":               int(entity_profile.get("is_pep", False)),
        "ent_pep_proximity":        int(entity_profile.get("pep_proximity_hops", 3) <= 2),
        "ent_sanctions_hit":        int(entity_profile.get("sanctions_hit", False)),
        "ent_adverse_media_score":  round(entity_profile.get("adverse_media_score", 0), 3),
        "ent_watchlist_score":      round(entity_profile.get("watchlist_score", 0), 3),

        # Business nature
        "ent_is_cash_intensive":    int(entity_profile.get("is_cash_intensive_business", False)),
        "ent_is_msb":               int(entity_profile.get("is_msb", False)),
        "ent_is_shell_company":     int(entity_profile.get("shell_company_indicators", 0) > 0),
        "ent_business_age_years":   round(entity_profile.get("business_age_years", 5), 1),
        "ent_employee_count":       int(entity_profile.get("employee_count", 10)),
        "ent_revenue_txn_ratio":    round(entity_profile.get("revenue_txn_ratio", 1.0), 3),

        # Jurisdiction
        "ent_country_risk_idx":     int(entity_profile.get("country_risk_idx", 0)),
        "ent_fatf_grey_list":       int(entity_profile.get("fatf_grey_list", False)),
        "ent_fatf_black_list":      int(entity_profile.get("fatf_black_list", False)),
        "ent_offshore_structure":   int(entity_profile.get("offshore_structure", False)),

        # Account characteristics
        "ent_account_age_days":     int(entity_profile.get("account_age_days", 365)),
        "ent_account_new":          int(entity_profile.get("account_age_days", 365) < 90),
        "ent_multi_account_flag":   int(entity_profile.get("account_count", 1) > 3),
        "ent_product_count":        int(entity_profile.get("product_count", 1)),

        # SAR / alert history
        "ent_prior_sars":           int(entity_profile.get("prior_sars", 0)),
        "ent_prior_alerts_90d":     int(entity_profile.get("prior_alerts_90d", 0)),
        "ent_prior_cleared_pct":    round(entity_profile.get("prior_cleared_pct", 0.5), 3),

        # Onboarding quality
        "ent_onboarding_risk_flag": int(entity_profile.get("onboarding_risk_flag", False)),
    }
    return features  # 28 features


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FEATURE VECTOR ASSEMBLER
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_features(
    txn: dict,
    history: list = None,
    entity_graph: dict = None,
    customer_profile: dict = None,
    peer_group_stats: dict = None,
    entity_profile: dict = None,
) -> dict:
    """
    Compute the complete 214-feature vector for a transaction.
    Uses safe defaults if optional inputs are missing.
    """
    history = history or []
    entity_graph = entity_graph or _default_graph(txn)
    customer_profile = customer_profile or _default_profile(txn)
    peer_group_stats = peer_group_stats or _default_peer_stats()
    entity_profile = entity_profile or _default_entity_profile(txn)

    features = {}
    features.update(compute_velocity_features(txn, history))
    features.update(compute_network_features(txn, entity_graph))
    features.update(compute_behavioural_features(txn, customer_profile, peer_group_stats))
    features.update(compute_typology_features(txn, history))
    features.update(compute_entity_risk_features(txn, entity_profile))

    return features


def get_feature_summary() -> dict:
    """Return summary statistics about the feature set."""
    return {
        "total_features": 214,
        "categories": {
            "Velocity & Volume": {"count": 52, "prefix": "vel_"},
            "Network / Graph": {"count": 48, "prefix": "net_"},
            "Behavioural Baseline": {"count": 44, "prefix": "beh_"},
            "Typology Indicators": {"count": 42, "prefix": "typ_"},
            "Entity Risk Context": {"count": 28, "prefix": "ent_"},
        },
        "fincen_priority_coverage": [
            "Structuring (vel_, typ_near_10k*, typ_structuring*)",
            "Layering (net_*, typ_layering*, typ_rapid_move*)",
            "Sanctions (ent_sanctions*, typ_ofac*, ent_fatf*)",
            "Crypto/Virtual assets (typ_virtual_asset*, typ_crypto*)",
            "Trade-based AML (typ_invoice*, typ_trade*, typ_over*)",
            "Terrorism financing (typ_high_risk*, typ_charity*)",
            "Human trafficking (typ_elder*, typ_wage*, typ_cash*)",
            "Fraud/Cybercrime (typ_ato*, typ_new_device*, typ_credential*)",
        ]
    }


# ── Helper functions ──────────────────────────────────────────────────────────

def _days_ago(ts, now):
    if isinstance(ts, str):
        try: ts = datetime.fromisoformat(ts)
        except: return 0
    if isinstance(now, str):
        try: now = datetime.fromisoformat(now)
        except: now = datetime.now()
    return (now - ts).total_seconds() / 86400

def _safe_ratio(a, b):
    return a / b if b else 0

def _step_up(history, window_days, prior_days, now):
    recent = sum(h["amount"] for h in history if _days_ago(h.get("timestamp", now), now) <= window_days)
    prior  = sum(h["amount"] for h in history if window_days < _days_ago(h.get("timestamp", now), now) <= window_days + prior_days)
    return round(_safe_ratio(recent, prior or 1) - 1, 3)

def _count_within_hours(history, now, hours):
    return sum(1 for h in history if _days_ago(h.get("timestamp", now), now) * 24 <= hours)

def _daily_sum(history, now, days_ago_int):
    return sum(h["amount"] for h in history
               if int(_days_ago(h.get("timestamp", now), now)) == days_ago_int)

def _coeff_variation(vals):
    if len(vals) < 2: return 0
    mean = sum(vals) / len(vals)
    if mean == 0: return 0
    std = math.sqrt(sum((v - mean)**2 for v in vals) / len(vals))
    return std / mean

def _skewness(vals):
    if len(vals) < 3: return 0
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean)**2 for v in vals) / len(vals)) or 1
    return sum(((v - mean) / std)**3 for v in vals) / len(vals)

def _kurtosis(vals):
    if len(vals) < 4: return 0
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean)**2 for v in vals) / len(vals)) or 1
    return sum(((v - mean) / std)**4 for v in vals) / len(vals) - 3

def _round_dollar_pct(history):
    if not history: return 0
    return sum(1 for h in history if h["amount"] % 1000 == 0) / len(history)

def _avg_interarrival(history, now):
    if len(history) < 2: return 999
    times = sorted([_days_ago(h.get("timestamp", now), now) * 24 for h in history])
    diffs = [times[i+1] - times[i] for i in range(len(times)-1)]
    return sum(diffs) / len(diffs) if diffs else 999

def _min_interarrival(history, now):
    if len(history) < 2: return 999
    times = sorted([_days_ago(h.get("timestamp", now), now) * 24 for h in history])
    diffs = [times[i+1] - times[i] for i in range(len(times)-1)]
    return min(diffs) if diffs else 999

def _weekend_pct(history):
    if not history: return 0
    return sum(1 for h in history if h.get("day_of_week", 1) >= 5) / len(history)

def _offhours_pct(history):
    if not history: return 0
    return sum(1 for h in history if h.get("hour", 12) < 6 or h.get("hour", 12) > 22) / len(history)

def _night_count(history, now, days):
    return sum(1 for h in history
               if _days_ago(h.get("timestamp", now), now) <= days
               and (h.get("hour", 12) < 6 or h.get("hour", 12) > 22))

def _net_flow(history, days, now):
    flows = []
    for h in history:
        if _days_ago(h.get("timestamp", now), now) <= days:
            flows.append(h["amount"] if h.get("direction") == "in" else -h["amount"])
    return sum(flows)

def _median(vals):
    if not vals: return 0
    s = sorted(vals)
    n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2

def _pct_above(history, threshold, days, now):
    relevant = [h for h in history if _days_ago(h.get("timestamp", now), now) <= days]
    if not relevant: return 0
    return sum(1 for h in relevant if h["amount"] > threshold) / len(relevant)

def _herfindahl(amounts):
    total = sum(amounts) or 1
    return sum((a/total)**2 for a in amounts)

def _default_graph(txn):
    return {
        "counterparties": [{"amount": txn.get("amount", 0), "risk_score": txn.get("jurisdiction_idx", 0)*25, "jurisdiction_idx": txn.get("jurisdiction_idx", 0), "direction": "out", "days_known": 1 if txn.get("new_counterparty") else 100}],
        "hub_score": 0.2, "community_size": 3, "community_risk": 0.2,
        "betweenness": 0.1, "clustering": 0.3, "two_hop_degree": 5,
        "two_hop_high_risk": 0, "two_hop_sar_entities": 0,
        "round_trip_detected": False, "shell_score": 0.1,
        "cp_with_prior_sars": 0, "cross_institution_links": 0,
        "product_count": 1, "geo_spread_index": 0.2,
        "cross_border_pct": txn.get("cross_border", 0),
        "offshore_pct": 0.1 if txn.get("jurisdiction_idx", 0) >= 2 else 0,
        "cp_churn_rate": 0.1, "entity_age_days": txn.get("account_age_days", 365),
        "days_since_first_txn": txn.get("account_age_days", 365),
    }

def _default_profile(txn):
    return {
        "avg_amount": 25000, "std_amount": 15000, "median_amount": 18000,
        "typical_hour": 12, "typical_channel": txn.get("channel_idx", 0),
        "channel_variety": 2, "recent_channel_switch": False,
        "historical_cb_rate": 0.1, "historical_mc_rate": 0.05,
        "cb_frequency_change": 0, "recent_activity_ratio": 1,
        "pattern_break_score": 0, "dormant_reactivated": False,
        "new_product_days": 999, "new_cp_rate_30d": 0.1,
        "cp_churn_rate": 0.1, "cp_concentration_change": 0,
        "velocity_peer_pct": 50, "frequency_peer_pct": 50, "amount_peer_pct": 50,
        "peer_rank_pct": 50, "income_consistent": True, "spending_consistent": True,
        "occupation_match": True, "risk_score_trend": 0,
        "alert_count_90d": 0, "prior_fp_rate": 0.5, "prior_sar_rate": 0,
        "composite_anomaly_score": 0, "baseline_confidence": 0.7,
        "months_of_history": 24, "account_age_days": txn.get("account_age_days", 365),
        "days_since_last_alert": 999, "alert_count_30d": 0,
    }

def _default_peer_stats():
    return {"mean_amount": 20000, "std_amount": 12000, "cross_border_rate": 0.15}

def _default_entity_profile(txn):
    return {
        "risk_tier_idx": txn.get("tier_idx", 1),
        "kyc_risk_score": txn.get("tier_idx", 1) * 25,
        "days_since_cdd_refresh": 180,
        "kyc_completeness": 0.95,
        "is_pep": txn.get("tier_idx", 0) >= 3,
        "pep_proximity_hops": 3,
        "sanctions_hit": txn.get("jurisdiction_idx", 0) >= 3,
        "adverse_media_score": 0,
        "watchlist_score": 0,
        "is_cash_intensive_business": txn.get("channel_idx", 0) == 1,
        "is_msb": False,
        "shell_company_indicators": 0,
        "business_age_years": 5,
        "employee_count": 50,
        "revenue_txn_ratio": 1.0,
        "country_risk_idx": txn.get("jurisdiction_idx", 0),
        "fatf_grey_list": txn.get("jurisdiction_idx", 0) >= 2,
        "fatf_black_list": txn.get("jurisdiction_idx", 0) >= 3,
        "offshore_structure": txn.get("cross_border", 0) == 1,
        "account_age_days": txn.get("account_age_days", 365),
        "account_count": 1,
        "product_count": txn.get("multi_currency", 0) + 1,
        "prior_sars": txn.get("prior_sars", 0),
        "prior_alerts_90d": 0,
        "prior_cleared_pct": 0.5,
        "onboarding_risk_flag": txn.get("account_age_days", 365) < 30,
    }
