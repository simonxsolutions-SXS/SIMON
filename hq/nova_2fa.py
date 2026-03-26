"""
nova_2fa.py — Simon-X Solutions / NOVA
TOTP-based Two-Factor Authentication for admin operations.

Security properties:
  - RFC 6238 TOTP (SHA-1, 6 digits, 30s window)
  - Replay attack protection: each token usable ONCE within its window
  - Rate limiting: max 5 failed attempts per 5 minutes per action
  - Fail-SECURE: missing module / unconfigured = block, never allow
  - Full audit log to /home/simon-hq/logs/nova_2fa_audit.log
  - Constant-time comparison (hmac.compare_digest) — no timing attacks
"""

import os
import json
import time
import hmac
import hashlib
import base64
import struct
import logging
import threading
from pathlib import Path
from typing import Optional
from collections import defaultdict

# ── Audit logger (separate from app logger) ───────────────────────────────────
_LOG_DIR = Path(os.environ.get("NOVA_LOG_DIR", "/home/simon-hq/logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _audit_handler = logging.FileHandler(_LOG_DIR / "nova_2fa_audit.log")
except (PermissionError, OSError):
    import tempfile
    _audit_handler = logging.FileHandler(
        Path(tempfile.gettempdir()) / "nova_2fa_audit.log"
    )
_audit_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logger = logging.getLogger("nova_2fa")
logger.setLevel(logging.DEBUG)
logger.addHandler(_audit_handler)
# Also log to stderr so journalctl picks it up
logger.addHandler(logging.StreamHandler())

# ── Config ────────────────────────────────────────────────────────────────────
_CONFIG_PATH = Path(os.environ.get("NOVA_CONFIG", "/home/simon-hq/simon-hq/nova_config.json"))
_2FA_KEY = "totp_secret"

# ── Admin-classified actions ──────────────────────────────────────────────────
ADMIN_ACTIONS: set[str] = {
    "nova_run_command",         # python3/docker/git — arbitrary code execution
    "nova_service_restart",
    "nova_service_stop",
    "nova_service_start",
    "nova_service_enable",
    "nova_service_disable",
    "nova_file_delete",
    "nova_file_write",
    "nova_file_move",
    "nova_file_chmod",
    "nova_firewall_change",
    "nova_config_write",
    "nova_config_set",
    "nova_apt_install",
    "nova_apt_remove",
    "nova_ssh_execute",
    "nova_tailscale_config",
    "nova_port_open",
    "nova_port_close",
    "nova_email_send",
    "nova_db_execute",
    "nova_db_drop",
    "nova_db_backup_delete",
    "hud_restart_service",
    "hud_delete_file",
    "hud_run_install",
    "hud_apply_config",
    # Note: nova_360_report intentionally excluded — read-only diagnostic
}

SAFE_ACTIONS: set[str] = {
    "nova_chat", "nova_system_info", "nova_service_status",
    "nova_file_read", "nova_file_search", "nova_disk_usage",
    "nova_email_inbox", "nova_email_search", "nova_libreoffice_info",
    "nova_chroma_query", "nova_chroma_list",
}


class TwoFactorAuth:
    """
    TOTP authenticator — RFC 6238, stdlib only, no external dependencies.

    Security hardening:
      _used_tokens  : dict[token_str -> used_at_timestamp]
                      Tokens are single-use. A code accepted once is rejected
                      for all subsequent attempts within the 90s replay window.
      _failed_attempts: rate-limit tracker per action key.
                      5 failures within 300s locks that action for 60s.
      _lock         : threading.Lock — all state mutations are thread-safe.
    """

    # Replay window: reject reuse within this many seconds (3× the TOTP period)
    REPLAY_WINDOW_SECS = 90
    # Rate limit: max failures before lockout
    RATE_LIMIT_MAX = 5
    RATE_LIMIT_WINDOW = 300   # 5 minutes
    RATE_LIMIT_LOCKOUT = 60   # 1 minute lockout after hitting limit

    def __init__(self):
        self._secret: Optional[str] = None
        self._lock = threading.Lock()
        # token -> timestamp it was first accepted
        self._used_tokens: dict[str, float] = {}
        # action -> list of failure timestamps
        self._failed_attempts: dict[str, list] = defaultdict(list)
        self._load_secret()

    # ── Secret management ──────────────────────────────────────────────────────

    def _load_secret(self):
        try:
            if _CONFIG_PATH.exists():
                cfg = json.loads(_CONFIG_PATH.read_text())
                self._secret = cfg.get(_2FA_KEY)
        except Exception as e:
            logger.error(f"LOAD_SECRET_FAILED | {e}")

    def _save_secret(self, secret: str):
        try:
            cfg = {}
            if _CONFIG_PATH.exists():
                cfg = json.loads(_CONFIG_PATH.read_text())
            cfg[_2FA_KEY] = secret
            _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
            _CONFIG_PATH.chmod(0o600)
            self._secret = secret
            logger.info("SECRET_SAVED | nova_config.json updated, permissions set 600")
        except Exception as e:
            logger.error(f"SECRET_SAVE_FAILED | {e}")
            raise

    def generate_secret(self) -> str:
        raw = os.urandom(20)
        secret = base64.b32encode(raw).decode("utf-8")
        self._save_secret(secret)
        return secret

    def is_configured(self) -> bool:
        self._load_secret()
        return bool(self._secret)

    # ── TOTP core (RFC 6238 / RFC 4226) ───────────────────────────────────────

    def _hotp(self, key_b32: str, counter: int) -> str:
        key = base64.b32decode(key_b32.upper())
        msg = struct.pack(">Q", counter)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        offset = h[-1] & 0x0F
        code = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
        return str(code % 1_000_000).zfill(6)

    def _totp(self, key_b32: str, timestamp: Optional[float] = None, step: int = 30) -> str:
        t = int((timestamp or time.time()) / step)
        return self._hotp(key_b32, t)

    # ── Replay protection helpers ──────────────────────────────────────────────

    def _purge_expired_tokens(self):
        """Remove tokens outside the replay window. Call inside lock."""
        cutoff = time.time() - self.REPLAY_WINDOW_SECS
        expired = [tok for tok, ts in self._used_tokens.items() if ts < cutoff]
        for tok in expired:
            del self._used_tokens[tok]

    def _is_replay(self, token: str) -> bool:
        """Returns True if this token was already used. Call inside lock."""
        self._purge_expired_tokens()
        return token in self._used_tokens

    def _mark_used(self, token: str):
        """Record token as used. Call inside lock."""
        self._used_tokens[token] = time.time()

    # ── Rate limiting helpers ──────────────────────────────────────────────────

    def _is_rate_limited(self, action: str) -> tuple[bool, int]:
        """
        Returns (is_locked, seconds_remaining).
        Purges old failures outside the window first.
        """
        now = time.time()
        cutoff = now - self.RATE_LIMIT_WINDOW
        attempts = self._failed_attempts[action]
        # Purge old failures
        self._failed_attempts[action] = [t for t in attempts if t > cutoff]
        recent = self._failed_attempts[action]

        if len(recent) >= self.RATE_LIMIT_MAX:
            last_fail = max(recent)
            locked_until = last_fail + self.RATE_LIMIT_LOCKOUT
            if now < locked_until:
                return True, int(locked_until - now)
        return False, 0

    def _record_failure(self, action: str):
        """Record a failed attempt for rate limiting."""
        self._failed_attempts[action].append(time.time())

    # ── Main verify method ─────────────────────────────────────────────────────

    def verify(self, token: str, action: str = "unknown", window: int = 1) -> tuple[bool, str]:
        """
        Verify a 6-digit TOTP token with full security checks.

        Returns (True, "authorized") or (False, "reason").

        Security checks in order:
          1. Format validation (6 digits)
          2. Rate limit check (5 failures / 5min)
          3. Secret configured check
          4. Replay attack check (single-use tokens)
          5. TOTP math verification (constant-time)
        """
        token = token.strip().replace(" ", "")

        # 1. Format check
        if len(token) != 6 or not token.isdigit():
            logger.warning(f"INVALID_FORMAT | action={action} | token_len={len(token)}")
            return False, "Token must be exactly 6 digits."

        with self._lock:
            # 2. Rate limit
            locked, secs = self._is_rate_limited(action)
            if locked:
                logger.error(f"RATE_LIMITED | action={action} | locked_for={secs}s")
                return False, f"Too many failed attempts. Try again in {secs} seconds."

            # 3. Secret check
            if not self._secret:
                self._load_secret()
            if not self._secret:
                logger.error(f"NO_SECRET | action={action}")
                return False, "2FA not configured. Run nova_2fa_setup.py first."

            # 4. Replay check
            if self._is_replay(token):
                logger.error(f"REPLAY_BLOCKED | action={action} | token=[REDACTED]")
                return False, "This code has already been used. Wait for the next 30-second code."

            # 5. TOTP math — check current window ±1 step for clock drift.
            #    We iterate ALL drift values without breaking early so that
            #    timing does not reveal which window matched (constant-time
            #    across all drift positions).
            t_now = time.time()
            verified = False
            for drift in range(-window, window + 1):
                expected = self._totp(self._secret, t_now + drift * 30)
                # compare_digest is constant-time; accumulate result without
                # short-circuiting to prevent timing oracle across drift steps.
                if hmac.compare_digest(token, expected):
                    verified = True
                    # Do NOT break — iterate all steps for constant-time behaviour

            if verified:
                self._mark_used(token)  # Single-use: mark immediately
                logger.info(f"AUTHORIZED | action={action} | token=[REDACTED]")
                return True, "authorized"
            else:
                self._record_failure(action)
                fails = len(self._failed_attempts[action])
                remaining = self.RATE_LIMIT_MAX - fails
                logger.warning(f"DENIED | action={action} | token=[REDACTED] | fails={fails}")
                if remaining <= 1:
                    return False, f"Invalid token. Warning: {remaining} attempt left before lockout."
                return False, f"Invalid or expired token. ({remaining} attempts remaining)"

    # ── QR / Setup helpers ─────────────────────────────────────────────────────

    def get_otpauth_uri(self, account: str = "admin@your-domain.com",
                        issuer: str = "NOVA-SimonX") -> str:
        if not self._secret:
            raise RuntimeError("No TOTP secret configured.")
        return (
            f"otpauth://totp/{issuer}:{account}"
            f"?secret={self._secret}"
            f"&issuer={issuer}"
            f"&algorithm=SHA1&digits=6&period=30"
        )

    def print_setup_instructions(self, account: str = "admin@your-domain.com"):
        uri = self.get_otpauth_uri(account)
        print("\n" + "=" * 60)
        print("  NOVA 2FA Setup — Google Authenticator")
        print("=" * 60)
        print(f"\n  Secret key (manual entry):\n  {self._secret}")
        print(f"\n  OTPAuth URI:\n  {uri}")
        try:
            import qrcode
            qr = qrcode.QRCode()
            qr.add_data(uri)
            qr.make()
            print("\n  QR Code (scan with Google Authenticator):\n")
            qr.print_ascii(invert=True)
        except ImportError:
            print("\n  pip install qrcode  — for QR in terminal")
            print("  Or open this URL in a browser: https://qr.io")
        print("\n  Account : NOVA-SimonX | Period: 30s | SHA-1 | 6 digits")
        print("=" * 60 + "\n")

    def get_qr_png_bytes(self) -> Optional[bytes]:
        try:
            import qrcode, io
            img = qrcode.make(self.get_otpauth_uri())
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            return None


# ── Module-level singleton ────────────────────────────────────────────────────
_auth = TwoFactorAuth()


def is_admin_action(action: str) -> bool:
    return action in ADMIN_ACTIONS


def require_admin_auth(token: str, action: str) -> tuple[bool, str]:
    """
    Primary gate for all admin operations. Fail-SECURE in every path.

    Returns (True, "authorized") or (False, reason_string).
    """
    if not is_admin_action(action):
        return True, "action does not require 2FA"

    # Fail-SECURE: not configured = block
    if not _auth.is_configured():
        logger.error(f"NOT_CONFIGURED | action={action} | BLOCKED")
        return False, (
            "🔐 2FA is not configured — all admin operations are blocked.\n"
            "Run on simon-hq:  python3 ~/simon-hq/nova_2fa_setup.py\n"
            "Then scan the QR with Google Authenticator and retry."
        )

    # No token provided
    if not token:
        logger.warning(f"NO_TOKEN | action={action} | BLOCKED")
        return False, (
            f"🔐 '{action}' requires your Google Authenticator code.\n"
            "Ask SIMON: 'I need to verify' — or provide the 6-digit code directly."
        )

    return _auth.verify(token, action=action)


def get_2fa_status() -> dict:
    return {
        "configured": _auth.is_configured(),
        "admin_actions_protected": len(ADMIN_ACTIONS),
        "algorithm": "TOTP/SHA1",
        "period_seconds": 30,
        "digits": 6,
        "replay_protection": True,
        "rate_limit": f"{TwoFactorAuth.RATE_LIMIT_MAX} attempts / {TwoFactorAuth.RATE_LIMIT_WINDOW}s",
        "compatible_apps": ["Google Authenticator", "Authy", "1Password", "Bitwarden"],
    }
