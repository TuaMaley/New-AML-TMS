"""
AML-TMS Investigation Module
===============================
Handles:
  - SAR narrative drafts with revision history
  - Document attachments (base64 stored in-memory)
  - Entity relationship graph data
  - Email notification system
"""
import uuid, base64, json, re, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

# ── SAR Draft Store ──────────────────────────────────────────────────────────
# { case_id: { "current": {...}, "revisions": [...] } }
SAR_DRAFTS = {}

def save_sar_draft(case_id: str, content: str, officer: str,
                   title: str = None, status: str = "draft") -> dict:
    """
    Save a SAR narrative draft. Keeps full revision history.
    content: HTML or plain text narrative
    """
    now = datetime.now().isoformat()
    revision = {
        "revision_id":  str(uuid.uuid4())[:8],
        "saved_at":     now,
        "saved_by":     officer,
        "status":       status,
        "word_count":   len(re.sub(r'<[^>]+>', '', content).split()),
        "char_count":   len(content),
        "content":      content,
    }
    if case_id not in SAR_DRAFTS:
        SAR_DRAFTS[case_id] = {
            "case_id":     case_id,
            "title":       title or f"SAR Draft — {case_id}",
            "created_at":  now,
            "created_by":  officer,
            "revisions":   [],
            "current":     revision,
        }
    else:
        # Push current to revisions history (keep last 20)
        prev = SAR_DRAFTS[case_id].get("current")
        if prev:
            SAR_DRAFTS[case_id]["revisions"].insert(0, prev)
            SAR_DRAFTS[case_id]["revisions"] = SAR_DRAFTS[case_id]["revisions"][:20]
        SAR_DRAFTS[case_id]["current"] = revision
        if title:
            SAR_DRAFTS[case_id]["title"] = title

    return {
        "ok":            True,
        "case_id":       case_id,
        "revision_id":   revision["revision_id"],
        "saved_at":      now,
        "revision_count":len(SAR_DRAFTS[case_id]["revisions"]),
        "word_count":    revision["word_count"],
    }

def get_sar_draft(case_id: str) -> dict:
    return SAR_DRAFTS.get(case_id)

def get_sar_revision(case_id: str, revision_id: str) -> dict:
    draft = SAR_DRAFTS.get(case_id)
    if not draft: return None
    if draft["current"]["revision_id"] == revision_id:
        return draft["current"]
    return next((r for r in draft["revisions"]
                 if r["revision_id"] == revision_id), None)

def list_sar_drafts() -> list:
    return [
        {
            "case_id":       v["case_id"],
            "title":         v["title"],
            "created_at":    v["created_at"],
            "created_by":    v["created_by"],
            "last_saved":    v["current"]["saved_at"],
            "last_saved_by": v["current"]["saved_by"],
            "status":        v["current"]["status"],
            "revision_count":len(v["revisions"]),
            "word_count":    v["current"]["word_count"],
        }
        for v in SAR_DRAFTS.values()
    ]


# ── Document Attachment Store ─────────────────────────────────────────────────
# { case_id: [ {doc_id, filename, size, uploaded_by, ...} ] }
DOCUMENTS = defaultdict(list)
DOCUMENT_DATA = {}  # { doc_id: bytes } separate to avoid serializing large blobs

MAX_DOC_SIZE = 5 * 1024 * 1024  # 5MB per doc
MAX_DOCS_PER_CASE = 20

ALLOWED_TYPES = {
    "application/pdf":        ".pdf",
    "image/png":              ".png",
    "image/jpeg":             ".jpg",
    "application/msword":     ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain":             ".txt",
    "text/csv":               ".csv",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

def upload_document(case_id: str, filename: str, content_type: str,
                    data_b64: str, uploaded_by: str,
                    description: str = "") -> dict:
    """
    Attach a document to a case.
    data_b64: base64-encoded file content
    """
    if len(DOCUMENTS[case_id]) >= MAX_DOCS_PER_CASE:
        return {"ok": False, "error": f"Maximum {MAX_DOCS_PER_CASE} documents per case"}

    # Decode and size-check
    try:
        data = base64.b64decode(data_b64)
    except Exception:
        return {"ok": False, "error": "Invalid base64 data"}

    if len(data) > MAX_DOC_SIZE:
        return {"ok": False, "error": f"File too large (max 5MB, got {len(data)//1024}KB)"}

    doc_id = str(uuid.uuid4())[:12]
    doc = {
        "doc_id":       doc_id,
        "case_id":      case_id,
        "filename":     filename,
        "content_type": content_type,
        "size_bytes":   len(data),
        "size_kb":      round(len(data) / 1024, 1),
        "uploaded_by":  uploaded_by,
        "uploaded_at":  datetime.now().isoformat(),
        "description":  description,
        "category":     _categorize_doc(filename, description),
    }
    DOCUMENTS[case_id].append(doc)
    DOCUMENT_DATA[doc_id] = data

    return {"ok": True, "doc_id": doc_id, "doc": doc}

def get_documents(case_id: str) -> list:
    return DOCUMENTS.get(case_id, [])

def get_document_data(doc_id: str) -> tuple:
    """Returns (data_bytes, doc_meta) or (None, None)"""
    data = DOCUMENT_DATA.get(doc_id)
    if not data: return None, None
    for docs in DOCUMENTS.values():
        for d in docs:
            if d["doc_id"] == doc_id:
                return data, d
    return data, None

def delete_document(case_id: str, doc_id: str, deleted_by: str) -> dict:
    docs = DOCUMENTS.get(case_id, [])
    before = len(docs)
    DOCUMENTS[case_id] = [d for d in docs if d["doc_id"] != doc_id]
    DOCUMENT_DATA.pop(doc_id, None)
    return {"ok": len(DOCUMENTS[case_id]) < before, "deleted_by": deleted_by}

def _categorize_doc(filename: str, description: str) -> str:
    combined = (filename + " " + description).lower()
    if any(x in combined for x in ["bank", "statement", "transaction", "ledger"]):
        return "Bank Statement"
    if any(x in combined for x in ["kyc", "identity", "passport", "id ", "cdd"]):
        return "KYC/Identity"
    if any(x in combined for x in ["invoice", "trade", "shipment", "bill"]):
        return "Trade Document"
    if any(x in combined for x in ["sar", "report", "filing", "narrative"]):
        return "SAR Document"
    if any(x in combined for x in ["court", "legal", "judgment", "warrant"]):
        return "Legal Document"
    if any(x in combined for x in ["screen", "sanction", "ofac", "watchlist"]):
        return "Screening Result"
    return "Supporting Evidence"


# ── Entity Relationship Graph ─────────────────────────────────────────────────
def build_entity_graph(case_id: str, alerts: list, cases: list) -> dict:
    """
    Build a node-link graph for visualisation.
    Returns D3-compatible {nodes: [...], links: [...]} structure.
    """
    nodes = {}
    links = []
    link_ids = set()

    def add_node(node_id, label, node_type, risk=0, extra=None):
        if node_id not in nodes:
            nodes[node_id] = {
                "id":    node_id,
                "label": label,
                "type":  node_type,   # entity, account, transaction, institution, person
                "risk":  risk,
                "size":  _node_size(node_type, risk),
                "color": _node_color(node_type, risk),
                **(extra or {}),
            }

    def add_link(source, target, label="", amount=0, link_type="transaction"):
        key = f"{source}|{target}"
        rkey = f"{target}|{source}"
        if key not in link_ids and rkey not in link_ids:
            links.append({
                "source":    source,
                "target":    target,
                "label":     label,
                "amount":    amount,
                "type":      link_type,
                "width":     max(1, min(6, int(amount / 100000))) if amount else 2,
                "color":     "#E24B4A" if link_type == "suspicious" else
                             "#8892AA" if link_type == "normal" else "#1E50B4",
            })
            link_ids.add(key)

    # Central entity (subject of the case)
    subject = None
    for c in cases:
        if c.get("id") == case_id:
            subject = c.get("entity","Unknown Entity")
            break
    if not subject and alerts:
        subject = alerts[0].get("entity","Unknown Entity")

    subject_id = "subj_0"
    add_node(subject_id, subject, "entity", risk=85,
             extra={"is_subject": True, "case_id": case_id})

    # Add institution node
    add_node("inst_0", "First National\nCompliance Bank", "institution", risk=0)
    add_link("inst_0", subject_id, "customer relationship", link_type="normal")

    # Build counterparty nodes from alert transactions
    cp_counter = 0
    seen_cps = {}
    for alert in alerts:
        alert_risk = alert.get("score", 50)
        for txn in alert.get("transactions", []):
            desc = txn.get("desc", "")
            amount = abs(txn.get("amount", 0))
            direction = txn.get("dir", "out")

            # Extract counterparty from description
            cp_name = _extract_counterparty(desc)
            if not cp_name: continue

            if cp_name not in seen_cps:
                cp_id = f"cp_{cp_counter}"
                seen_cps[cp_name] = cp_id
                cp_counter += 1
                cp_risk = _counterparty_risk(cp_name, desc)
                add_node(cp_id, cp_name, "entity", risk=cp_risk,
                         extra={"is_counterparty": True})
            else:
                cp_id = seen_cps[cp_name]

            ltype = "suspicious" if alert_risk >= 70 else "normal"
            if direction == "out":
                add_link(subject_id, cp_id, f"${amount:,.0f}", amount, ltype)
            else:
                add_link(cp_id, subject_id, f"${amount:,.0f}", amount, ltype)

    # Add related accounts
    for i, alert in enumerate(alerts[:3]):
        acct_id = f"acct_{i}"
        add_node(acct_id, f"Account\n****{1000+i*111}", "account",
                 risk=alert.get("score", 50))
        add_link(subject_id, acct_id, "holds", link_type="normal")

    # Add a second-hop entity for layering illustration if high-risk
    high_risk_cps = [cp for cp, nid in seen_cps.items()
                     if nodes.get(nid, {}).get("risk", 0) >= 70]
    for i, cp_name in enumerate(high_risk_cps[:2]):
        cp_id = seen_cps[cp_name]
        shell_id = f"shell_{i}"
        add_node(shell_id, f"Shell Co. {i+1}\n(2nd hop)", "entity", risk=60,
                 extra={"is_shell": True, "opacity": 0.7})
        add_link(cp_id, shell_id, "transfer", link_type="suspicious")

    return {
        "nodes":     list(nodes.values()),
        "links":     links,
        "subject_id": subject_id,
        "stats": {
            "node_count": len(nodes),
            "link_count":  len(links),
            "high_risk_nodes": sum(1 for n in nodes.values() if n["risk"] >= 70),
            "suspicious_links": sum(1 for l in links if l["type"] == "suspicious"),
            "total_flow": sum(l["amount"] for l in links),
        }
    }

def _extract_counterparty(desc: str) -> str:
    for sep in ["→", "←", "->", "<-", "to ", "from "]:
        if sep in desc:
            parts = desc.split(sep)
            name = parts[-1].strip() if sep in ("→","->","to ") else parts[0].strip()
            name = re.sub(r'\b(wire|transfer|payment|deposit|outflow|inflow)\b', '',
                          name, flags=re.I).strip()
            if len(name) > 2:
                return name[:30]
    return ""

def _counterparty_risk(name: str, desc: str) -> int:
    risk = 30
    high_risk_terms = ["panama", "cayman", "bvi", "offshore", "shell", "russia",
                       "iran", "dprk", "crypto", "exchange", "unknown", "llc"]
    for term in high_risk_terms:
        if term in name.lower() or term in desc.lower():
            risk += 15
    return min(95, risk)

def _node_size(node_type: str, risk: int) -> int:
    base = {"entity": 20, "account": 14, "institution": 24,
            "transaction": 10, "person": 18}.get(node_type, 16)
    return base + int(risk / 20)

def _node_color(node_type: str, risk: int) -> str:
    if node_type == "institution": return "#0F6E56"
    if node_type == "account":     return "#7832B4"
    if risk >= 80: return "#E24B4A"
    if risk >= 60: return "#D05538"
    if risk >= 40: return "#BA7517"
    return "#1E50B4"


# ── Email Notification System ─────────────────────────────────────────────────
# In-memory notification store (replaces actual SMTP for cloud demo)
NOTIFICATIONS = []
EMAIL_CONFIG = {
    "enabled":     False,  # Set to True with real SMTP credentials
    "smtp_host":   "",
    "smtp_port":   587,
    "username":    "",
    "password":    "",
    "from_addr":   "aml-tms@yourdomain.com",
    "recipients":  [],
}

def configure_email(config: dict) -> dict:
    """Update email configuration."""
    EMAIL_CONFIG.update(config)
    return {"ok": True, "enabled": EMAIL_CONFIG["enabled"],
            "recipients": EMAIL_CONFIG["recipients"]}

def send_notification(event_type: str, subject: str, body: str,
                      data: dict = None, recipients: list = None) -> dict:
    """
    Send (or queue) a notification.
    Stores in NOTIFICATIONS regardless of SMTP config.
    """
    notif = {
        "id":          str(uuid.uuid4())[:8],
        "event_type":  event_type,
        "subject":     subject,
        "body":        body,
        "data":        data or {},
        "sent_at":     datetime.now().isoformat(),
        "recipients":  recipients or EMAIL_CONFIG.get("recipients", []),
        "delivered":   False,
        "error":       None,
    }

    # Try actual SMTP if configured
    if EMAIL_CONFIG["enabled"] and EMAIL_CONFIG["smtp_host"] and notif["recipients"]:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[AML-TMS] {subject}"
            msg["From"]    = EMAIL_CONFIG["from_addr"]
            msg["To"]      = ", ".join(notif["recipients"])
            msg.attach(MIMEText(body, "plain"))
            html_body = _format_html_email(subject, body, data)
            msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP(EMAIL_CONFIG["smtp_host"], EMAIL_CONFIG["smtp_port"]) as s:
                s.starttls()
                s.login(EMAIL_CONFIG["username"], EMAIL_CONFIG["password"])
                s.send_message(msg)
            notif["delivered"] = True
        except Exception as e:
            notif["error"] = str(e)
    else:
        # Simulated delivery for demo
        notif["delivered"] = True
        notif["note"] = "Simulated — configure SMTP to send real emails"

    NOTIFICATIONS.insert(0, notif)
    return {"ok": True, "notification_id": notif["id"], "delivered": notif["delivered"]}

def _format_html_email(subject: str, body: str, data: dict) -> str:
    rows = ""
    for k, v in (data or {}).items():
        rows += f"<tr><td style='padding:6px 12px;color:#666'>{k}</td><td style='padding:6px 12px;font-weight:500'>{v}</td></tr>"
    return f"""
<div style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto'>
  <div style='background:#0F285A;padding:20px;border-radius:8px 8px 0 0'>
    <h2 style='color:#fff;margin:0'>AML-TMS Alert</h2>
    <p style='color:rgba(255,255,255,.7);margin:4px 0 0'>{subject}</p>
  </div>
  <div style='background:#f9f9f9;padding:20px;border:1px solid #e0e0e0'>
    <p style='color:#333;line-height:1.6'>{body.replace(chr(10),'<br>')}</p>
    {f'<table style="width:100%;border-collapse:collapse;margin-top:16px;background:#fff;border:1px solid #e0e0e0;border-radius:6px">{rows}</table>' if rows else ''}
  </div>
  <div style='background:#f0f0f0;padding:12px 20px;border-radius:0 0 8px 8px;font-size:12px;color:#999'>
    AML-TMS Platform · Automated notification · Do not reply
  </div>
</div>"""

def trigger_alert_notification(alert: dict) -> dict:
    """Send notification for a new critical/high alert."""
    priority = alert.get("priority","")
    if priority not in ("critical","high"): return {"ok": False, "reason": "Not high priority"}
    return send_notification(
        event_type="NEW_ALERT",
        subject=f"{priority.upper()} Alert — {alert.get('entity','')} (Score: {alert.get('score',0)})",
        body=f"A {priority} risk alert has been generated.\n\nEntity: {alert.get('entity','')}\nAmount: ${alert.get('amount',0):,.2f}\nRisk Score: {alert.get('score',0)}/100\nTypology: {alert.get('typology','')}\nChannel: {alert.get('channel','')}\n\nImmediate investigation is required.",
        data={"Alert ID": alert.get("id"), "Entity": alert.get("entity"),
              "Score": f"{alert.get('score',0)}/100", "Priority": priority.upper(),
              "Amount": f"${alert.get('amount',0):,.2f}", "Typology": alert.get("typology","")},
    )

def trigger_sar_deadline_notification(case: dict, days_remaining: int) -> dict:
    return send_notification(
        event_type="SAR_DEADLINE",
        subject=f"SAR Deadline Warning — {case.get('entity','')} ({days_remaining} days remaining)",
        body=f"SAR filing deadline approaching.\n\nCase: {case.get('id','')}\nEntity: {case.get('entity','')}\nDays remaining: {days_remaining}\n\nPlease complete SAR preparation immediately.",
        data={"Case ID": case.get("id"), "Entity": case.get("entity"),
              "Days Remaining": str(days_remaining), "Officer": case.get("officer","Unassigned")},
    )

def trigger_sanctions_hit_notification(entity: str, match: dict) -> dict:
    return send_notification(
        event_type="SANCTIONS_HIT",
        subject=f"SANCTIONS HIT — {entity} matches SDN entry",
        body=f"A sanctions screening hit has been detected.\n\nQueried Entity: {entity}\nSDN Match: {match.get('sdn_name','')}\nMatch Score: {match.get('match_score',0)}%\nPrograms: {', '.join(match.get('programs',[]))}\nReason: {match.get('reason','')}\n\nImmediately freeze account and escalate to Compliance Officer.",
        data={"Entity": entity, "SDN Name": match.get("sdn_name"),
              "Score": f"{match.get('match_score',0)}%", "Programs": ", ".join(match.get("programs",[]))},
    )

def get_notifications(limit: int = 50, event_type: str = None) -> list:
    notifs = NOTIFICATIONS
    if event_type:
        notifs = [n for n in notifs if n["event_type"] == event_type]
    return notifs[:limit]

def get_notification_stats() -> dict:
    types = {}
    for n in NOTIFICATIONS:
        t = n["event_type"]
        types[t] = types.get(t, 0) + 1
    return {
        "total":       len(NOTIFICATIONS),
        "unread":      sum(1 for n in NOTIFICATIONS if not n.get("read")),
        "by_type":     types,
        "smtp_enabled":EMAIL_CONFIG["enabled"],
        "recipients":  EMAIL_CONFIG["recipients"],
    }
