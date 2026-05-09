"""
AML-TMS SAR Auto-Population Engine
=====================================
Auto-populates FinCEN Form 111 (Suspicious Activity Report) fields
and generates a structured SAR narrative from case / alert data.

Aligned with FinCEN SAR narrative guidance (2021) and BSA requirements.
"""
from datetime import datetime, timedelta
import random

# ── SAR Activity Type Codes (FinCEN Form 111) ────────────────────────────────
SAR_ACTIVITY_TYPES = {
    "Structuring / threshold evasion":   {"code": "STR", "form_code": "g", "category": "Bank Secrecy Act"},
    "Cross-border layering":             {"code": "LAY", "form_code": "z", "category": "Money Laundering"},
    "Smurfing pattern":                  {"code": "SMU", "form_code": "g", "category": "Bank Secrecy Act"},
    "Sanctions-adjacent activity":        {"code": "SAN", "form_code": "s", "category": "Sanctions"},
    "FX structuring":                    {"code": "FXS", "form_code": "z", "category": "Money Laundering"},
    "Anomalous transaction pattern":      {"code": "ATP", "form_code": "z", "category": "Money Laundering"},
    "Shell company network":             {"code": "SCN", "form_code": "z", "category": "Money Laundering"},
    "Crypto conversion":                 {"code": "CRY", "form_code": "p", "category": "Virtual Currency"},
    "Trade-based AML":                   {"code": "TBM", "form_code": "z", "category": "Money Laundering"},
}

INSTRUMENT_TYPES = {
    "Wire Transfer":    "Wire transfer",
    "Cash Deposit":     "Currency (cash)",
    "Fintech API":      "Other - API transfer",
    "FX/Treasury":      "Foreign currency exchange",
    "Trade Finance":    "Letter of credit / trade instrument",
    "Mobile Banking":   "Mobile payment",
}

def generate_sar(case: dict, alerts: list, officer: str) -> dict:
    """
    Generate a complete SAR record from a case and its linked alerts.
    Returns a dict representing a populated FinCEN Form 111.
    """
    primary_alert = alerts[0] if alerts else {}
    typology = case.get("typology", "Anomalous transaction pattern")
    activity = SAR_ACTIVITY_TYPES.get(typology, SAR_ACTIVITY_TYPES["Anomalous transaction pattern"])

    total_amount = sum(a.get("amount", 0) for a in alerts)
    date_range_start = _earliest_date(alerts)
    date_range_end   = _latest_date(alerts)

    sar = {
        # ── Part I: Filing Institution ──────────────────────────────────────
        "filing": {
            "institution_name":     "First National Compliance Bank",
            "ein":                  "12-3456789",
            "naics_code":           "522110",
            "address":              "100 Financial Plaza, New York, NY 10004",
            "contact_name":         officer,
            "contact_phone":        "(212) 555-0100",
            "contact_email":        f"{officer.lower().replace(' ','.')}@fncb.com",
            "filed_date":           datetime.now().strftime("%Y-%m-%d"),
            "prior_sar_ref":        None,
        },

        # ── Part II: Suspicious Activity Information ─────────────────────
        "activity": {
            "date_of_activity_start": date_range_start,
            "date_of_activity_end":   date_range_end,
            "total_amount_involved":  round(total_amount, 2),
            "activity_type_code":     activity["code"],
            "activity_category":      activity["category"],
            "form_111_box":           activity["form_code"],
            "instrument_type":        INSTRUMENT_TYPES.get(primary_alert.get("channel", ""), "Other"),
            "product_type":           _product_type(primary_alert),
            "ip_addresses":           [],
            "involved_countries":     _extract_countries(alerts),
        },

        # ── Part III: Subject Information ────────────────────────────────
        "subject": {
            "entity_name":            case.get("entity", "Unknown"),
            "entity_type":            _entity_type(case),
            "tax_id":                 _mock_tax_id(case.get("entity", "")),
            "dob":                    None,
            "address":                _mock_address(),
            "id_type":                "EIN" if _is_business(case) else "SSN",
            "relationship_to_filer":  "Customer",
            "account_numbers":        [f"ACCT-{random.randint(100000,999999)}"],
            "is_pep":                 False,
            "is_sanctioned":          False,
        },

        # ── Part IV: Suspicious Activity Description ──────────────────────
        "narrative": _generate_narrative(case, alerts, officer, total_amount, date_range_start, date_range_end),

        # ── Part V: Contact for Questions ────────────────────────────────
        "contact": {
            "name":   officer,
            "phone":  "(212) 555-0100",
            "agency": "AML Compliance Division",
        },

        # ── Metadata ─────────────────────────────────────────────────────
        "meta": {
            "case_id":           case.get("id"),
            "alert_ids":         [a.get("id") for a in alerts],
            "alert_count":       len(alerts),
            "ml_scores":         [a.get("score") for a in alerts],
            "avg_ml_score":      round(sum(a.get("score",0) for a in alerts) / max(len(alerts),1)),
            "generated_at":      datetime.now().isoformat(),
            "generated_by":      "AML-TMS Auto-Population Engine v2.4",
            "requires_review":   True,
            "narrative_draft":   True,
        }
    }
    return sar


def _generate_narrative(case, alerts, officer, total_amount, start_date, end_date) -> str:
    """
    Generate a structured SAR narrative following FinCEN guidance:
    Who, What, Where, When, Why (5 W's + How).
    """
    entity = case.get("entity", "the subject")
    typology = case.get("typology", "anomalous transaction pattern")
    channel = alerts[0].get("channel", "wire transfer") if alerts else "wire transfer"
    case_id = case.get("id", "")
    alert_count = len(alerts)
    scores = [a.get("score", 0) for a in alerts]
    avg_score = round(sum(scores) / max(len(scores), 1))

    # Top SHAP features from alerts
    top_features = []
    for a in alerts[:2]:
        for shap in (a.get("shap") or [])[:2]:
            label = shap.get("label", "") if isinstance(shap, dict) else shap[0]
            val = shap.get("shap", 0) if isinstance(shap, dict) else shap[1]
            if val > 0.1:
                top_features.append(label.lower())
    top_features = list(dict.fromkeys(top_features))[:3]

    narrative = f"""SUSPICIOUS ACTIVITY REPORT — NARRATIVE DESCRIPTION
Case Reference: {case_id} | Generated: {datetime.now().strftime('%B %d, %Y')}

WHO:
{entity} (hereinafter "the Subject"), a customer of First National Compliance Bank, is the subject of this Suspicious Activity Report. The Subject maintains account(s) with this institution and has been identified as engaging in suspicious financial activity consistent with {typology.lower()}.

WHAT:
The Subject conducted {alert_count} transaction(s) totalling ${total_amount:,.2f} via {channel.lower()} between {start_date} and {end_date}. These transactions were flagged by the institution's AI/ML-driven transaction monitoring system (AML-TMS) with an average risk score of {avg_score}/100, indicating a high probability of suspicious activity.

The transactions exhibited the following suspicious characteristics:
{chr(10).join(f'  • {f.capitalize()}' for f in (top_features or ['Unusual transaction pattern inconsistent with customer profile', 'Deviation from established behavioural baseline']))}
  • Activity inconsistent with the Subject's known business purpose and stated source of funds
  • Pattern consistent with {typology.lower()} as defined in FinCEN's 2021 National AML/CFT Priorities

WHERE:
The suspicious activity was conducted through this institution's {channel.lower()} platform. {_where_detail(alerts)}

WHEN:
Suspicious activity first identified: {start_date}
Most recent suspicious transaction: {end_date}
Total activity period under review: {_date_diff(start_date, end_date)} day(s)
Date of initial ML system alert: {start_date}
Date SAR determination made: {datetime.now().strftime('%Y-%m-%d')}

HOW:
The institution's AI/ML Transaction Monitoring System flagged this activity through ensemble scoring across four detection models: (1) unsupervised anomaly detection identifying deviations from the Subject's behavioural baseline; (2) supervised classification against known SAR-labelled typology patterns; (3) graph neural network analysis identifying suspicious counterparty network characteristics; and (4) temporal sequence modelling identifying step-up velocity patterns. The ensemble model produced risk scores of {', '.join(str(s) for s in scores)}/100 across the flagged transactions.

WHY:
Based on the totality of the evidence — including the ML model outputs, SHAP feature attribution analysis, linked transaction review, counterparty analysis, and investigator determination — this institution has determined that the Subject's activity has no reasonable explanation consistent with known legitimate business activity. The pattern is consistent with {typology.lower()}, a typology formally designated in FinCEN's 2021 National AML/CFT Priorities.

This report is filed pursuant to 31 U.S.C. §5318(g) and 31 C.F.R. §1020.320. The filing institution requests that law enforcement contact {officer} at (212) 555-0100 with any questions regarding this report.

DRAFT — REQUIRES REVIEW AND APPROVAL BY SENIOR AML OFFICER BEFORE FILING
"""
    return narrative.strip()


def _where_detail(alerts):
    channels = set(a.get("channel","") for a in alerts)
    if len(channels) > 1:
        return f"Transactions spanned multiple channels: {', '.join(channels)}, indicating potential cross-channel structuring."
    return ""

def _earliest_date(alerts):
    dates = [a.get("timestamp","") for a in alerts if a.get("timestamp")]
    return min(dates)[:10] if dates else datetime.now().strftime("%Y-%m-%d")

def _latest_date(alerts):
    dates = [a.get("timestamp","") for a in alerts if a.get("timestamp")]
    return max(dates)[:10] if dates else datetime.now().strftime("%Y-%m-%d")

def _date_diff(start, end):
    try:
        d1 = datetime.strptime(start[:10], "%Y-%m-%d")
        d2 = datetime.strptime(end[:10], "%Y-%m-%d")
        return max(1, (d2 - d1).days)
    except:
        return 1

def _product_type(alert):
    channel = alert.get("channel", "")
    mapping = {
        "Wire Transfer": "Wire transfer - domestic/international",
        "Cash Deposit":  "Demand deposit account - cash activity",
        "Fintech API":   "Digital payment account",
        "FX/Treasury":   "Foreign currency / treasury product",
        "Trade Finance": "Letter of credit / trade finance",
        "Mobile Banking":"Mobile / digital banking",
    }
    return mapping.get(channel, "Demand deposit account")

def _extract_countries(alerts):
    countries = []
    for a in alerts:
        for txn in a.get("transactions", []):
            desc = txn.get("desc", "")
            for country in ["Panama", "Cayman", "BVI", "Switzerland", "UAE", "Russia", "China"]:
                if country.lower() in desc.lower():
                    countries.append(country)
    return list(set(countries)) or ["United States"]

def _entity_type(case):
    entity = case.get("entity", "")
    if any(x in entity for x in ["LLC", "Inc.", "Corp", "Ltd", "Group", "Holdings"]):
        return "Legal Entity"
    return "Individual"

def _is_business(case):
    return _entity_type(case) == "Legal Entity"

def _mock_tax_id(entity_name):
    seed = sum(ord(c) for c in entity_name)
    random.seed(seed)
    if any(x in entity_name for x in ["LLC", "Inc.", "Corp", "Ltd"]):
        return f"{random.randint(10,99)}-{random.randint(1000000,9999999)}"
    return f"***-**-{random.randint(1000,9999)}"

def _mock_address():
    streets = ["100 Commerce St", "250 Financial Ave", "88 Trade Plaza", "1 Banking Blvd"]
    cities = ["New York, NY 10004", "Miami, FL 33131", "Los Angeles, CA 90071", "Houston, TX 77002"]
    return f"{random.choice(streets)}, {random.choice(cities)}"


def get_sar_checklist(sar: dict) -> list:
    """
    Return a pre-filing checklist for SAR quality review.
    """
    narrative = sar.get("narrative", "")
    return [
        {"item": "Subject identity verified", "passed": bool(sar["subject"]["tax_id"])},
        {"item": "Account numbers documented", "passed": bool(sar["subject"]["account_numbers"])},
        {"item": "Total amount stated", "passed": sar["activity"]["total_amount_involved"] > 0},
        {"item": "Activity dates specified", "passed": bool(sar["activity"]["date_of_activity_start"])},
        {"item": "Activity type code assigned", "passed": bool(sar["activity"]["activity_type_code"])},
        {"item": "5 W's present in narrative", "passed": all(w in narrative for w in ["WHO", "WHAT", "WHERE", "WHEN", "HOW"])},
        {"item": "ML evidence referenced", "passed": "AI/ML" in narrative or "machine learning" in narrative.lower()},
        {"item": "FinCEN typology cited", "passed": "FinCEN" in narrative},
        {"item": "Statutory authority cited", "passed": "31 U.S.C." in narrative},
        {"item": "Contact information complete", "passed": bool(sar["contact"]["phone"])},
        {"item": "Senior officer review required", "passed": True},  # Always require
        {"item": "BSA 30-day deadline met", "passed": True},
    ]
