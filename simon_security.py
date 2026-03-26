"""
S.I.M.O.N. Security Guard — simon_security.py
===============================================
Passive security enforcement layer. Not a plugin — imported by jarvis.py directly.

Three jobs:
  1. scan_for_sensitive(text)       → find credentials/PII in any string
  2. is_safe_to_send(text, to)      → approve/block outbound messages
  3. SHELL_BLOCKLIST                → imported by tool_run_shell

This module enforces security at the CODE level — independent of the LLM.
Even if the model is tricked or compromised, these checks still fire.
"""

import re
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
#  SENSITIVE DATA PATTERNS
#  Any text matching these should never leave the machine via send tools.
# ─────────────────────────────────────────────────────────────────────────────

_SENSITIVE_PATTERNS = [

    # ── Credentials ──────────────────────────────────────────────────────────
    (r"(?i)(password|passwd|pwd)\s*(?:[:=]|is|:|=)\s*\S+",
     "password field"),

    (r"(?i)(api[_\-\s]?key|apikey|api[_\-\s]?token|access[_\-\s]?token|auth[_\-\s]?token|bearer\s+\S+)",
     "API key / token"),

    (r"(?i)(secret[_\-\s]?key|client[_\-\s]?secret|app[_\-\s]?secret)",
     "secret key"),

    (r"(?i)(private[_\-\s]?key|-----BEGIN\s+(RSA\s+)?PRIVATE KEY)",
     "private key"),

    (r"ghp_[A-Za-z0-9]{36}",
     "GitHub personal access token"),

    (r"sk-[A-Za-z0-9]{20,}",
     "OpenAI / Anthropic API key"),

    (r"(?i)(ollama[_\-\s]?key|ollama[_\-\s]?cloud)",
     "Ollama API key reference"),

    # ── Identity / Financial ─────────────────────────────────────────────────
    (r"\b\d{3}-\d{2}-\d{4}\b",
     "Social Security Number (SSN)"),

    (r"\b\d{9}\b",
     "possible SSN (9-digit number)"),

    (r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b",
     "credit card number"),

    (r"(?i)(routing\s+number|account\s+number|bank\s+account)\s*[:=]?\s*\d{8,17}",
     "bank account / routing number"),

    (r"(?i)(passport\s+number|passport\s+#)\s*[:=]?\s*[A-Z0-9]{6,9}",
     "passport number"),

    (r"(?i)(driver'?s?\s*licen[sc]e|DL\s+#)\s*[:=]?\s*[A-Z0-9\-]{5,15}",
     "driver's license number"),

    # ── Network / Infrastructure ─────────────────────────────────────────────
    # Private IP ranges — don't send network topology externally
    (r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
     "private IPv4 address (10.x.x.x)"),

    (r"\b(192\.168\.\d{1,3}\.\d{1,3})\b",
     "private IPv4 address (192.168.x.x)"),

    (r"\b(172\.(1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3})\b",
     "private IPv4 address (172.16-31.x.x)"),

    # MAC addresses — device fingerprints
    (r"\b([0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}\b",
     "MAC address (device identifier)"),

    # SSH private keys
    (r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
     "SSH/TLS private key"),

    # AWS credentials
    (r"AKIA[0-9A-Z]{16}",
     "AWS Access Key ID"),

    (r"(?i)aws[_\-\s]?secret[_\-\s]?access[_\-\s]?key\s*[:=]\s*\S{40}",
     "AWS Secret Access Key"),

    # Config file contents
    (r"(?i)(config\.json|\.env|\.pem|id_rsa|\.key)\b",
     "credential/config file reference"),

    # ── Health / Legal ────────────────────────────────────────────────────────
    (r"(?i)(diagnosis|medical\s+record|HIPAA|PHI\b|patient\s+id)",
     "medical / HIPAA-protected information"),

    (r"(?i)(attorney.client|privileged\s+communication|work\s+product)",
     "legally privileged communication"),
]

# Pre-compile all patterns once at import time
_COMPILED = [(re.compile(pat), label) for pat, label in _SENSITIVE_PATTERNS]


def scan_for_sensitive(text: str) -> list[dict]:
    """
    Scan a string for sensitive data patterns.

    Returns a list of findings:
      [{"pattern": "API key / token", "match": "sk-abc...", "redacted": "sk-[REDACTED]"}, ...]

    Empty list = clean.
    """
    if not text:
        return []

    findings = []
    seen_labels = set()   # de-duplicate by label type

    for compiled, label in _COMPILED:
        match = compiled.search(text)
        if match:
            if label in seen_labels:
                continue
            seen_labels.add(label)
            raw = match.group(0)
            # Redact the actual value — show only the label and first 6 chars
            safe_preview = raw[:6] + "..." if len(raw) > 6 else raw[:3] + "..."
            findings.append({
                "pattern": label,
                "match":   raw,
                "preview": safe_preview,
            })

    return findings


def redact_sensitive(text: str) -> str:
    """
    Return a copy of text with all sensitive values replaced by [REDACTED].
    Safe to log.
    """
    out = text
    for compiled, label in _COMPILED:
        out = compiled.sub(f"[{label.upper().replace(' ','_')}_REDACTED]", out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  OUTBOUND SEND GUARD
#  Called before any iMessage or email goes out.
# ─────────────────────────────────────────────────────────────────────────────

# Known safe recipients — SIMON's owner, no confirmation needed for these
# Populated from config.json by jarvis.py at startup
_TRUSTED_NUMBERS: set[str] = set()
_TRUSTED_EMAILS:  set[str] = set()


def register_trusted_contact(value: str) -> None:
    """Register a phone number or email as a trusted recipient."""
    v = value.strip().lower()
    if "@" in v:
        _TRUSTED_EMAILS.add(v)
    else:
        # Normalize to digits only
        digits = re.sub(r"[^\d]", "", v)
        _TRUSTED_NUMBERS.add(digits[-10:] if len(digits) >= 10 else digits)


def _normalize_recipient(to: str) -> str:
    """Normalize a phone/email for comparison against trusted set."""
    if "@" in to:
        return to.strip().lower()
    digits = re.sub(r"[^\d]", "", to)
    return digits[-10:] if len(digits) >= 10 else digits


def is_safe_to_send(content: str, recipient: str) -> dict:
    """
    Pre-flight check before sending any iMessage or email.

    Returns:
      {
        "safe":     bool,
        "reason":   str,           # human-readable explanation
        "findings": list[dict],    # sensitive data found (empty if clean)
        "trusted":  bool,          # is recipient in trusted list?
      }

    SIMON should refuse if safe=False.
    SIMON should warn (but can proceed with explicit confirmation) if trusted=False.
    """
    findings = scan_for_sensitive(content)
    rec_norm = _normalize_recipient(recipient)
    trusted  = (
        rec_norm in _TRUSTED_NUMBERS or
        rec_norm in _TRUSTED_EMAILS
    )

    if findings:
        labels = ", ".join({f["pattern"] for f in findings})
        return {
            "safe":     False,
            "reason":   f"Message contains sensitive data: {labels}. Sending blocked.",
            "findings": findings,
            "trusted":  trusted,
        }

    # Warn (not block) for untrusted recipients with network data
    net_data_patterns = [
        r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",   # any IP
        r"port\s+\d+\s+(?:open|closed)",               # port scan results
        r"\d+\s+device",                                # ARP scan results
        r"download.*mbps|upload.*mbps",                 # speed test
    ]
    has_net_data = any(
        re.search(p, content, re.IGNORECASE)
        for p in net_data_patterns
    )

    if has_net_data and not trusted:
        return {
            "safe":     False,
            "reason":   (
                "Message contains network diagnostic data (IPs, port scan results, "
                "or speed test data) and the recipient is not a trusted contact. "
                "Sending blocked to prevent infrastructure data leakage."
            ),
            "findings": [],
            "trusted":  False,
        }

    return {"safe": True, "reason": "OK", "findings": [], "trusted": trusted}


# ─────────────────────────────────────────────────────────────────────────────
#  SHELL BLOCKLIST
#  Imported by tool_run_shell — replaces the minimal existing list.
# ─────────────────────────────────────────────────────────────────────────────

SHELL_BLOCKLIST = [

    # ── Destructive ──────────────────────────────────────────────────────────
    "rm -rf",
    "rm -r /",
    "rm -f /",
    "mkfs",
    "dd if=",
    ":(){ :",           # fork bomb
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "sudo rm",
    "chmod 777",
    "chmod -R 777",
    ">/dev/",
    "format",
    "diskutil erasedisk",
    "diskutil reformat",

    # ── Privilege escalation ──────────────────────────────────────────────────
    "sudo -s",
    "sudo -i",
    "sudo su",
    "su root",
    "su -",
    "sudo bash",
    "sudo zsh",
    "sudo sh",
    "pkexec",
    "doas",

    # ── Credential harvesting ─────────────────────────────────────────────────
    "cat /etc/shadow",
    "cat /etc/passwd",
    "cat ~/.ssh/id_rsa",
    "cat ~/.ssh/id_ed25519",
    "cat ~/.aws/credentials",
    "cat ~/.netrc",
    "cat config.json",          # SIMON's own config with API key
    "security find-generic-password",
    "security dump-keychain",
    "security find-internet-password",
    "keychain",
    "cat *.pem",
    "cat *.key",
    "cat *.p12",
    "cat *.pfx",

    # ── Data exfiltration ─────────────────────────────────────────────────────
    "curl -X POST",
    "curl --data",
    "wget --post",
    "wget --post-data",
    "nc -e",                    # netcat reverse shell
    "bash -i",                  # interactive reverse shell
    "python -c.*socket",        # python reverse shell pattern
    "/dev/tcp/",                # bash TCP redirect (exfil)
    "base64 -d | bash",
    "base64 -D | bash",
    "| bash",                   # piping to bash (code execution from web)
    "| sh",
    "eval $(curl",
    "eval $(wget",

    # ── Surveillance / reconnaissance ─────────────────────────────────────────
    "tcpdump",
    "wireshark",
    "tshark",
    "dsniff",
    "ettercap",
    "nmap",                     # use built-in port scanner instead
    "masscan",

    # ── System tampering ──────────────────────────────────────────────────────
    "launchctl unload",
    "launchctl remove",
    "systemctl disable",
    "chown root",
    "chflags schg",             # macOS immutable flag
    "csrutil disable",          # disable SIP
    "nvram boot-args",
    "defaults write com.apple",
    "/etc/hosts",

    # ── Crypto / ransomware patterns ──────────────────────────────────────────
    "openssl enc",
    "gpg --encrypt",
    "openssl rand | xargs",
]


def is_safe_command(command: str) -> tuple[bool, str]:
    """
    Check a shell command against the blocklist.

    Returns (True, "") if safe, or (False, reason) if blocked.
    """
    cmd_lower = command.lower()
    for pattern in SHELL_BLOCKLIST:
        if pattern.lower() in cmd_lower:
            return False, f"Blocked pattern: '{pattern}'"
    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
#  PROMPT INJECTION DETECTOR
#  Scans incoming user messages for injection attempts before LLM sees them.
# ─────────────────────────────────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above|earlier)",
    r"forget\s+(everything|all)\s+(you\s+)?(know|were told)",
    r"you\s+are\s+now\s+(?!simon|s\.i\.m\.o\.n)",   # role hijack
    r"new\s+(system\s+)?instructions?\s*:",
    r"override\s+(system|security|safety)\s*(prompt|instructions?|rules?)?",
    r"pretend\s+(you\s+are|to\s+be)\s+(?!simon)",
    r"act\s+as\s+(?!simon|s\.i\.m\.o\.n)",
    r"jailbreak",
    r"DAN\s*(mode|prompt)?",            # "Do Anything Now" jailbreak
    r"developer\s+mode",
    r"sudo\s+mode",
    r"god\s+mode",
    r"reveal\s+(your|the)\s+(system\s+)?prompt",
    r"show\s+(me\s+)?(your|the)\s+(system\s+)?prompt",
    r"print\s+(your|the)\s+(system\s+)?prompt",
    r"what\s+(are\s+)?your\s+(full\s+)?(instructions?|prompt|rules?)",
    r"send\s+(my|the)\s+(password|credentials?|api\s*key|token|secret)",
    r"text.*my\s+(password|credentials?|pin|ssn|social\s+security)",
    r"email.*my\s+(password|credentials?|api\s*key|token|secret)",
]

_COMPILED_INJECTION = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def detect_injection(text: str) -> list[str]:
    """
    Scan user input for prompt injection / social engineering patterns.

    Returns list of matched pattern descriptions (empty = clean).
    """
    matches = []
    for i, pattern in enumerate(_COMPILED_INJECTION):
        if pattern.search(text):
            matches.append(_INJECTION_PATTERNS[i])
    return matches
