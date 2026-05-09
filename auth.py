"""
AML-TMS User & Access Management Module
==========================================
Handles authentication, roles, sessions, 2FA, and RBAC.

Roles & permissions:
  analyst    — view alerts/cases, investigate, add notes
  supervisor — all analyst + clear alerts, approve SAR, manage cases
  admin      — all supervisor + manage users, configure system, view all audit
  readonly   — view only, no actions

2FA: TOTP-style 6-digit code (demo: code is always the last 6 digits of
     the timestamp — in production use pyotp + authenticator app).
"""
import uuid, hashlib, secrets, time, re
from datetime import datetime, timedelta
from collections import defaultdict

# ── Permission matrix ─────────────────────────────────────────────────────────
PERMISSIONS = {
    "analyst": {
        "view_dashboard", "view_alerts", "investigate_alert",
        "add_case_note", "view_cases", "view_documents",
        "upload_document", "view_sar_drafts", "edit_sar_draft",
        "view_entity_graph", "view_sanctions", "run_sanctions_screen",
        "view_analytics", "view_notifications",
    },
    "supervisor": {
        # All analyst permissions plus:
        "view_dashboard", "view_alerts", "investigate_alert",
        "add_case_note", "view_cases", "view_documents",
        "upload_document", "view_sar_drafts", "edit_sar_draft",
        "view_entity_graph", "view_sanctions", "run_sanctions_screen",
        "view_analytics", "view_notifications",
        # Supervisor extras:
        "clear_alert", "escalate_alert", "create_case",
        "update_case_status", "approve_sar", "file_sar",
        "delete_document", "send_notification",
        "view_audit_log", "view_all_cases",
    },
    "admin": {
        # All permissions
        "view_dashboard", "view_alerts", "investigate_alert",
        "add_case_note", "view_cases", "view_documents",
        "upload_document", "view_sar_drafts", "edit_sar_draft",
        "view_entity_graph", "view_sanctions", "run_sanctions_screen",
        "view_analytics", "view_notifications",
        "clear_alert", "escalate_alert", "create_case",
        "update_case_status", "approve_sar", "file_sar",
        "delete_document", "send_notification",
        "view_audit_log", "view_all_cases",
        # Admin only:
        "manage_users", "configure_system", "configure_email",
        "trigger_retrain", "view_system_health", "manage_roles",
        "export_reports", "view_all_audit",
    },
    "readonly": {
        "view_dashboard", "view_alerts", "view_cases",
        "view_analytics", "view_sar_drafts",
    },
}

# ── Seed users ────────────────────────────────────────────────────────────────
def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

def _make_user(user_id, name, email, password, role, department="AML Compliance"):
    salt = secrets.token_hex(8)
    return {
        "user_id":      user_id,
        "name":         name,
        "email":        email,
        "password_hash":_hash_password(password, salt),
        "salt":         salt,
        "role":         role,
        "department":   department,
        "active":       True,
        "created_at":   datetime.now().isoformat(),
        "last_login":   None,
        "last_login_ip":None,
        "login_count":  0,
        "failed_logins":0,
        "locked":       False,
        "tfa_enabled":  False,
        "tfa_secret":   secrets.token_hex(10),
        "avatar":       name[0].upper(),
        "color":        ["#1E50B4","#0F6E56","#7832B4","#B8490A","#1EA064"][
                            hash(user_id) % 5],
    }

USERS = {
    "usr_admin":      _make_user("usr_admin",    "Ransford Adjapong", "r.adjapong@fncb.com",  "Admin@2024",    "admin",      "AML Compliance"),
    "usr_sup1":       _make_user("usr_sup1",     "J. Mensah",         "j.mensah@fncb.com",    "Super@2024",    "supervisor", "AML Compliance"),
    "usr_sup2":       _make_user("usr_sup2",     "B. Asante",         "b.asante@fncb.com",    "Super@2024",    "supervisor", "Financial Crime"),
    "usr_ana1":       _make_user("usr_ana1",     "A. Owusu",          "a.owusu@fncb.com",     "Analyst@2024",  "analyst",    "AML Compliance"),
    "usr_ana2":       _make_user("usr_ana2",     "K. Boateng",        "k.boateng@fncb.com",   "Analyst@2024",  "analyst",    "Financial Crime"),
    "usr_readonly":   _make_user("usr_readonly", "Compliance Viewer", "viewer@fncb.com",      "View@2024",     "readonly",   "Audit"),
}

# ── Session store ─────────────────────────────────────────────────────────────
SESSIONS = {}           # { token: { user_id, created_at, expires_at, ip, ... } }
SESSION_TTL = 86400     # 24 hours

# ── User audit trail ──────────────────────────────────────────────────────────
USER_AUDIT = defaultdict(list)   # { user_id: [ {ts, action, target, detail} ] }
FAILED_ATTEMPTS = defaultdict(int)
LOCKOUT_THRESHOLD = 5

# ── 2FA pending store ─────────────────────────────────────────────────────────
TFA_PENDING = {}   # { temp_token: { user_id, expires_at } }


def _log_user_action(user_id: str, action: str, target: str = "", detail: str = ""):
    USER_AUDIT[user_id].insert(0, {
        "ts":     datetime.now().isoformat(),
        "user_id":user_id,
        "action": action,
        "target": target,
        "detail": detail,
    })
    # Keep last 500 per user
    USER_AUDIT[user_id] = USER_AUDIT[user_id][:500]


# ── Authentication ────────────────────────────────────────────────────────────
def login(email: str, password: str, ip: str = "unknown") -> dict:
    """Authenticate user. Returns session token or 2FA challenge."""
    user = next((u for u in USERS.values()
                 if u["email"].lower() == email.lower()), None)

    if not user:
        return {"ok": False, "error": "Invalid email or password"}

    if user.get("locked"):
        return {"ok": False, "error": "Account locked — contact administrator"}

    if not user.get("active"):
        return {"ok": False, "error": "Account deactivated"}

    pw_hash = _hash_password(password, user["salt"])
    if pw_hash != user["password_hash"]:
        FAILED_ATTEMPTS[user["user_id"]] += 1
        if FAILED_ATTEMPTS[user["user_id"]] >= LOCKOUT_THRESHOLD:
            USERS[user["user_id"]]["locked"] = True
            _log_user_action(user["user_id"], "ACCOUNT_LOCKED",
                             detail=f"Locked after {LOCKOUT_THRESHOLD} failed attempts")
        remaining = LOCKOUT_THRESHOLD - FAILED_ATTEMPTS[user["user_id"]]
        return {"ok": False,
                "error": f"Invalid email or password ({max(0,remaining)} attempts remaining)"}

    # Reset failed attempts on success
    FAILED_ATTEMPTS[user["user_id"]] = 0

    # 2FA check
    if user.get("tfa_enabled"):
        temp = secrets.token_hex(16)
        TFA_PENDING[temp] = {
            "user_id":    user["user_id"],
            "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat(),
        }
        return {"ok": True, "requires_2fa": True, "temp_token": temp,
                "message": "Enter your 6-digit authentication code"}

    return _create_session(user, ip)


def verify_2fa(temp_token: str, code: str, ip: str = "unknown") -> dict:
    """Verify 2FA code and complete login."""
    pending = TFA_PENDING.get(temp_token)
    if not pending:
        return {"ok": False, "error": "Invalid or expired 2FA session"}

    if datetime.now().isoformat() > pending["expires_at"]:
        del TFA_PENDING[temp_token]
        return {"ok": False, "error": "2FA code expired — please log in again"}

    # Demo: valid code is any 6-digit number (in production: TOTP validation)
    if not re.match(r"^\d{6}$", str(code)):
        return {"ok": False, "error": "Invalid code format — enter 6 digits"}

    user = USERS.get(pending["user_id"])
    if not user:
        return {"ok": False, "error": "User not found"}

    del TFA_PENDING[temp_token]
    return _create_session(user, ip)


def _create_session(user: dict, ip: str) -> dict:
    token = secrets.token_hex(32)
    now = datetime.now()
    SESSIONS[token] = {
        "token":      token,
        "user_id":    user["user_id"],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=SESSION_TTL)).isoformat(),
        "ip":         ip,
        "last_active":now.isoformat(),
    }
    # Update user login info
    USERS[user["user_id"]]["last_login"]    = now.isoformat()
    USERS[user["user_id"]]["last_login_ip"] = ip
    USERS[user["user_id"]]["login_count"]  += 1
    _log_user_action(user["user_id"], "LOGIN", detail=f"From {ip}")

    return {
        "ok":          True,
        "token":       token,
        "user":        _safe_user(user),
        "permissions": sorted(PERMISSIONS.get(user["role"], [])),
        "role":        user["role"],
        "expires":     (now + timedelta(seconds=SESSION_TTL)).isoformat(),
    }


def logout(token: str) -> dict:
    session = SESSIONS.pop(token, None)
    if session:
        _log_user_action(session["user_id"], "LOGOUT")
    return {"ok": True}


def get_session(token: str) -> dict | None:
    """Validate session token. Returns session dict or None if invalid/expired."""
    session = SESSIONS.get(token)
    if not session:
        return None
    if datetime.now().isoformat() > session["expires_at"]:
        del SESSIONS[token]
        return None
    # Refresh last_active
    SESSIONS[token]["last_active"] = datetime.now().isoformat()
    return session


def get_current_user(token: str) -> dict | None:
    session = get_session(token)
    if not session:
        return None
    return USERS.get(session["user_id"])


def has_permission(token: str, permission: str) -> bool:
    user = get_current_user(token)
    if not user:
        return False
    return permission in PERMISSIONS.get(user["role"], set())


def require_permission(token: str, permission: str) -> tuple[bool, dict]:
    """Returns (allowed, error_response)"""
    if not token:
        return False, {"error": "Authentication required", "code": 401}
    user = get_current_user(token)
    if not user:
        return False, {"error": "Session expired — please log in", "code": 401}
    if permission not in PERMISSIONS.get(user["role"], set()):
        return False, {
            "error": f"Access denied — requires '{permission}' permission",
            "code":  403,
            "user_role": user["role"],
        }
    return True, {}


# ── User management (admin only) ──────────────────────────────────────────────
def create_user(name: str, email: str, password: str, role: str,
                department: str = "AML Compliance",
                created_by: str = "admin") -> dict:
    if role not in PERMISSIONS:
        return {"ok": False, "error": f"Invalid role: {role}"}
    if any(u["email"].lower() == email.lower() for u in USERS.values()):
        return {"ok": False, "error": "Email already registered"}
    if len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters"}

    user_id = f"usr_{uuid.uuid4().hex[:8]}"
    USERS[user_id] = _make_user(user_id, name, email, password, role, department)
    _log_user_action(created_by, "USER_CREATED", target=user_id,
                     detail=f"{name} ({email}) — role: {role}")
    return {"ok": True, "user_id": user_id, "user": _safe_user(USERS[user_id])}


def update_user(user_id: str, updates: dict, updated_by: str) -> dict:
    user = USERS.get(user_id)
    if not user:
        return {"ok": False, "error": "User not found"}
    allowed = {"name", "role", "department", "active", "tfa_enabled"}
    for k, v in updates.items():
        if k in allowed:
            user[k] = v
    if "password" in updates:
        salt = secrets.token_hex(8)
        user["salt"] = salt
        user["password_hash"] = _hash_password(updates["password"], salt)
    _log_user_action(updated_by, "USER_UPDATED", target=user_id,
                     detail=str({k: v for k, v in updates.items() if k != "password"}))
    return {"ok": True, "user": _safe_user(user)}


def unlock_user(user_id: str, unlocked_by: str) -> dict:
    user = USERS.get(user_id)
    if not user:
        return {"ok": False, "error": "User not found"}
    user["locked"] = False
    FAILED_ATTEMPTS[user_id] = 0
    _log_user_action(unlocked_by, "USER_UNLOCKED", target=user_id)
    return {"ok": True}


def toggle_2fa(user_id: str, enabled: bool) -> dict:
    user = USERS.get(user_id)
    if not user:
        return {"ok": False, "error": "User not found"}
    user["tfa_enabled"] = enabled
    _log_user_action(user_id, "2FA_TOGGLED", detail=f"{'Enabled' if enabled else 'Disabled'}")
    return {
        "ok":        True,
        "enabled":   enabled,
        "demo_note": "Demo mode: any 6-digit code is accepted. In production, scan QR code with Google Authenticator.",
        "qr_placeholder": f"otpauth://totp/AML-TMS:{user['email']}?secret={user['tfa_secret']}&issuer=AML-TMS",
    }


# ── User audit trail ──────────────────────────────────────────────────────────
def get_user_audit(user_id: str = None, limit: int = 100) -> list:
    if user_id:
        return USER_AUDIT.get(user_id, [])[:limit]
    # All users combined, sorted by timestamp
    all_entries = []
    for entries in USER_AUDIT.values():
        all_entries.extend(entries)
    all_entries.sort(key=lambda x: x["ts"], reverse=True)
    return all_entries[:limit]


def log_user_action(user_id: str, action: str, target: str = "", detail: str = ""):
    """Public wrapper for logging user actions from other modules."""
    _log_user_action(user_id, action, target, detail)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_user(user: dict) -> dict:
    """Return user dict without sensitive fields."""
    return {k: v for k, v in user.items()
            if k not in {"password_hash", "salt", "tfa_secret"}}


def list_users() -> list:
    return [_safe_user(u) for u in USERS.values()]


def get_user(user_id: str) -> dict | None:
    u = USERS.get(user_id)
    return _safe_user(u) if u else None


def get_active_sessions() -> list:
    now = datetime.now().isoformat()
    return [
        {**s, "user": _safe_user(USERS[s["user_id"]])}
        for s in SESSIONS.values()
        if s["expires_at"] > now
    ]


def get_role_permissions(role: str) -> list:
    return sorted(PERMISSIONS.get(role, []))


def get_all_roles() -> dict:
    return {role: sorted(perms) for role, perms in PERMISSIONS.items()}
