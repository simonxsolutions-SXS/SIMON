#!/usr/bin/env python3
"""
S.I.M.O.N. Plugin — Admin 2FA Guard
=====================================
Intercepts admin-level operations and requires Google Authenticator verification
before proceeding. SIMON will use fun, natural language to request the auth code.

Flow:
  1. User asks SIMON to do something admin-level (restart service, send email, etc.)
  2. SIMON calls request_admin_auth() → gets a fun prompt to show the user
  3. SIMON says: "🔐 Sending auth your way — magic pin please!"
  4. User checks Google Authenticator, types 6-digit code
  5. SIMON calls verify_admin_auth(token, action) → gets authorized/denied
  6. If authorized, SIMON proceeds with the original operation + passes the token

Verification: Calls simon-hq /api/admin/verify endpoint over Tailscale.
Fallback: Local nova_2fa module if simon-hq is unreachable.
"""

import random
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
_JARVIS_DIR  = Path(__file__).parent.parent
_CFG_PATH    = _JARVIS_DIR / "nova_config.json"
_HQ_VERIFY   = "http://YOUR_HQ_TAILSCALE_IP:3001/api/admin/verify"   # nova-hud on simon-hq

try:
    _cfg = json.loads(_CFG_PATH.read_text()) if _CFG_PATH.exists() else {}
    _HQ_VERIFY = _cfg.get("hq_2fa_verify_url", _HQ_VERIFY)
except Exception:
    _cfg = {}

# ── Fun auth request messages ─────────────────────────────────────────────────
_REQUEST_MSGS = [
    "🔐 Sending an auth request your way — **check your phone** and drop the magic pin!",
    "🛡️ Hold up — admin move detected. **Magic pin please!** What's Google Authenticator showing?",
    "📱 Before I do that, I need the magic pin. **Check your Authenticator** — what's the code?",
    "🔑 Authentication incoming to your phone! Drop the **magic pin** when you've got it.",
    "⚡ Admin op locked. **Check your Google Authenticator** and give me that magic pin!",
    "🛡️ That one needs your sign-off. **Magic pin please** — check your phone!",
    "🔐 I need to verify it's you. **What's the code on your Authenticator right now?**",
]

_DENIED_MSGS = [
    "❌ That pin didn't check out. Try again — make sure it's the freshest code showing.",
    "❌ Hmm, that's not matching. Code expires every 30 seconds — catch the next one and try again.",
    "❌ No match on that pin. Double-check you're on the **NOVA-SimonX** account in Authenticator.",
]

_APPROVED_MSGS = [
    "✅ **Verified!** You're good to go — proceeding now.",
    "✅ **Authenticated!** Magic pin accepted — let's do this.",
    "✅ **Identity confirmed.** Moving forward with the operation.",
]

# ── Admin action classifier ───────────────────────────────────────────────────
_ADMIN_KEYWORDS = [
    "restart", "stop service", "start service", "kill service",
    "delete file", "remove file", "write file", "overwrite",
    "send email", "email to", "send a message to",
    "install package", "apt install", "pip install",
    "firewall", "open port", "close port",
    "run install", "run script", "execute script",
    "system config", "change config", "update config",
    "database", "drop table", "delete records",
    "ssh into", "remote command",
]

def _is_admin_request(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _ADMIN_KEYWORDS)


# ── Plugin metadata ───────────────────────────────────────────────────────────
METADATA = {
    "name":        "Admin 2FA Guard",
    "description": "Google Authenticator verification for SIMON admin operations",
    "version":     "1.0",
    "author":      "Simon-X Solutions",
    "keywords": [
        # Explicit auth requests
        "verify me", "authenticate", "2fa", "two factor",
        "magic pin", "google authenticator", "auth code",
        # Admin operations (SIMON will call request_admin_auth before doing these)
        "restart service", "stop service", "start service",
        "delete file", "remove file", "send email",
        "install package", "run install", "open port",
        "check 2fa", "2fa status", "security status",
    ],
}

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "request_admin_auth",
            "description": (
                "CALL THIS BEFORE any admin-level operation (restart services, delete files, "
                "send email, install packages, change config, run scripts, etc.). "
                "Returns a prompt message for SIMON to show the user, asking for their "
                "Google Authenticator code. After calling this, present the returned message "
                "to the user and wait for their 6-digit code, then call verify_admin_auth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Brief description of the admin action being attempted, e.g. 'restart nova-mcpo service'"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_admin_auth",
            "description": (
                "Verify the 6-digit Google Authenticator code provided by the user. "
                "Call this after request_admin_auth and after the user has provided their code. "
                "If verification succeeds, the returned token should be passed as totp_token "
                "to any subsequent NOVA MCP tool calls that require it. "
                "If verification fails, do NOT proceed with the admin operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "token": {
                        "type": "string",
                        "description": "The 6-digit code from Google Authenticator"
                    },
                    "action": {
                        "type": "string",
                        "description": "The admin action being authorized (same as passed to request_admin_auth)"
                    }
                },
                "required": ["token", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_2fa_status",
            "description": (
                "Check whether 2FA is configured and active on simon-hq. "
                "Use when the user asks about security status, 2FA setup, or "
                "'is 2FA on?', 'is authentication active?', 'check security'."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]

# ── Verification logic ────────────────────────────────────────────────────────

def _verify_via_hq(token: str, action: str) -> tuple[bool, str]:
    """Call simon-hq's /api/admin/verify endpoint."""
    try:
        payload = json.dumps({"token": token, "action": action}).encode()
        req = urllib.request.Request(
            _HQ_VERIFY,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False), data.get("message", "")
    except urllib.error.URLError:
        return None, "unreachable"  # None = HQ unreachable, try local
    except Exception as e:
        return None, str(e)


def _verify_local(token: str) -> tuple[bool, str]:
    """Fallback: verify locally using nova_2fa module (needs secret in local config)."""
    try:
        sys.path.insert(0, str(_JARVIS_DIR))
        from nova_2fa import TwoFactorAuth
        auth = TwoFactorAuth()
        if not auth.is_configured():
            return False, "2FA not configured. Run: python3 ~/simon-hq/nova_2fa_setup.py"
        return auth.verify(token), "local verification"
    except ImportError:
        return False, "nova_2fa module not found"
    except Exception as e:
        return False, str(e)


def _check_status_via_hq() -> dict:
    """Fetch 2FA status from simon-hq HUD."""
    try:
        status_url = _HQ_VERIFY.replace("/api/admin/verify", "/api/admin/2fa-status")
        with urllib.request.urlopen(status_url, timeout=6) as resp:
            return json.loads(resp.read())
    except Exception:
        return {"configured": None, "available": False, "error": "simon-hq unreachable"}


# ── Execution ─────────────────────────────────────────────────────────────────

async def execute(name: str, args: dict):

    # ── request_admin_auth ────────────────────────────────────────────────────
    if name == "request_admin_auth":
        action = args.get("action", "admin operation")
        msg = random.choice(_REQUEST_MSGS)
        return (
            f"{msg}\n\n"
            f"*(Operation: **{action}**)*\n"
            f"Enter your 6-digit code and I'll get that done for you."
        )

    # ── verify_admin_auth ─────────────────────────────────────────────────────
    elif name == "verify_admin_auth":
        token = str(args.get("token", "")).strip().replace(" ", "")
        action = args.get("action", "admin_action")

        if len(token) != 6 or not token.isdigit():
            return "⚠️ That doesn't look like a valid 6-digit code. Try again — no spaces, just the numbers."

        # Try HQ endpoint first
        ok, msg = _verify_via_hq(token, action)

        if ok is None:
            # HQ unreachable — fall back to local
            ok, msg = _verify_local(token)
            source = "local"
        else:
            source = "simon-hq"

        if ok:
            approved_msg = random.choice(_APPROVED_MSGS)
            return (
                f"{approved_msg}\n"
                f"*(Verified via {source} | token: `{token}` | action: {action})*\n\n"
                f"**Pass `totp_token=\"{token}\"` to any NOVA tool that needs it.**"
            )
        else:
            denied_msg = random.choice(_DENIED_MSGS)
            return f"{denied_msg}\n*(Reason: {msg})*"

    # ── check_2fa_status ──────────────────────────────────────────────────────
    elif name == "check_2fa_status":
        status = _check_status_via_hq()

        if status.get("error"):
            return (
                f"⚠️ **2FA Status — simon-hq unreachable**\n"
                f"Could not reach simon-hq to check status.\n"
                f"Make sure simon-hq is online and nova-hud is running."
            )

        configured = status.get("configured", False)
        protected  = status.get("admin_actions_protected", 0)

        if configured:
            return (
                f"🔐 **2FA is ACTIVE** on simon-hq\n"
                f"  Algorithm : {status.get('algorithm', 'TOTP/SHA1')}\n"
                f"  Period    : {status.get('period_seconds', 30)}s tokens\n"
                f"  Protected : {protected} admin operations gated\n"
                f"  App       : Google Authenticator / Authy compatible\n\n"
                f"Any admin operation — service restarts, file writes, email sends — "
                f"will require your magic pin before executing."
            )
        else:
            return (
                f"⚠️ **2FA is NOT configured** on simon-hq\n\n"
                f"Admin operations are currently unprotected.\n"
                f"To enroll Google Authenticator, run on simon-hq:\n"
                f"```\npython3 ~/simon-hq/nova_2fa_setup.py\n```"
            )

    return None
