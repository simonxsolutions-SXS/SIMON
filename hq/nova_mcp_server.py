#!/usr/bin/env python3
"""
N.O.V.A. MCP Server — simon-hq | Simon-X Solutions
====================================================
Exposes Linux system, Android (ADB), network, memory, email,
LibreOffice, and direct Gmail tools via FastMCP stdio transport.
Wrapped by mcpo on port 8301 → Open WebUI tool server.

Tools:
  System      : nova_system_stats, nova_list_processes, nova_run_command,
                nova_read_file, nova_list_directory, nova_write_file
  Android     : nova_android_status, nova_android_send_sms,
                nova_android_notifications, nova_android_call_log,
                nova_android_battery_saver
  Network     : nova_ping, nova_tailscale_peers, nova_port_check,
                nova_active_connections, nova_network_interfaces
  Memory      : nova_memory_store, nova_memory_search, nova_memory_list
  Ollama      : nova_ollama_models, nova_ollama_chat
  Email       : nova_gmail_status, nova_gmail_read (Mac bridge)
                nova_email_send, nova_email_inbox, nova_email_search (direct SMTP/IMAP)
  LibreOffice : nova_libreoffice_convert, nova_libreoffice_info
  Files       : nova_file_search, nova_disk_usage
  Status      : nova_service_status
"""

import imaplib
import json
import os
import re as _re
import shlex as _shlex
import smtplib
import subprocess
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import message_from_bytes
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ── 2FA ───────────────────────────────────────────────────────────────────────
try:
    from nova_2fa import require_admin_auth, is_admin_action, get_2fa_status
    _2FA_AVAILABLE = True
except ImportError:
    _2FA_AVAILABLE = False
    def require_admin_auth(token, action):
        # Fail-SECURE: if the module is missing, block everything admin
        return False, (
            "🔐 BLOCKED: nova_2fa.py is not installed on simon-hq. "
            "Deploy it and restart: sudo systemctl restart nova-mcpo"
        )
    def is_admin_action(action):
        return True  # Assume admin if we can't check

# ── Config ────────────────────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "nova_config.json"
try:
    CFG = json.loads(_cfg_path.read_text())
except Exception:
    CFG = {}

OLLAMA_URL   = CFG.get("ollama_hq_url",  "http://127.0.0.1:11434")
HQ_API_URL   = CFG.get("hq_api_url",     "http://127.0.0.1:8200")
HQ_API_KEY   = CFG.get("hq_api_key",     "")
# Prefer MagicDNS hostname over raw IP when available in config
_mac_ts_host = CFG.get("mac_tailscale_host", CFG.get("mac_tailscale_ip", "YOUR_MAC_TAILSCALE_IP"))
MAC_URL      = CFG.get("mac_simon_url",  f"http://{_mac_ts_host}:8765")
ANDROID_IP   = CFG.get("android_ip",    "YOUR_ANDROID_TAILSCALE_IP")
ANDROID_PORT = CFG.get("android_port",  5555)

# Gmail direct access
# Priority: environment variable > nova_config.json
# Best practice: set GMAIL_USER and GMAIL_APP_PASS in the service EnvironmentFile
# (/etc/simon-hq/nova-mcpo.env) rather than nova_config.json.
GMAIL_USER     = os.getenv("GMAIL_USER",     CFG.get("gmail_user",     ""))
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS", CFG.get("gmail_app_pass", ""))
GMAIL_SMTP     = "smtp.gmail.com"
GMAIL_IMAP     = "imap.gmail.com"

CHROMA_HOST  = "127.0.0.1"
CHROMA_PORT  = 8100
CHROMA_V2    = f"http://{CHROMA_HOST}:{CHROMA_PORT}/api/v2"
CHROMA_COLS  = f"{CHROMA_V2}/tenants/default_tenant/databases/default_database/collections"
COLLECTION   = "nova_memory"

ADB_TARGET = f"{ANDROID_IP}:{ANDROID_PORT}"

# Commands allowed in nova_run_command (read-only ops + known tools)
_ALLOWED_CMDS = {
    "df", "du", "free", "uptime", "uname", "hostname",
    "ip", "ss", "netstat", "nmap", "ping", "traceroute",
    "ls", "cat", "find", "grep", "wc", "head", "tail", "less",
    "ps", "top", "lsof", "who", "w", "last", "htop",
    "systemctl", "journalctl", "dmesg", "service",
    "date", "cal", "env", "echo", "pwd",
    "adb", "curl", "wget", "nc",
    "libreoffice", "soffice",
    "python3", "python",
    "git", "docker",
    "sensors", "lscpu", "lsblk", "lsusb", "lspci",
    "ifconfig", "iwconfig", "nmcli",
    "tailscale",
}

# Commands that can execute arbitrary code — require 2FA even for read-like invocations
_HIGH_RISK_CMDS = {"python3", "python", "docker", "git"}

# Shell metacharacters that enable injection — block any command containing these
_SHELL_INJECT_RE = _re.compile(r'[;&|`$<>()\{\}\[\]]|&&|\|\|')

# FastMCP — stdio transport (mcpo wraps this process)
mcp = FastMCP("NOVA")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sh(cmd: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


def _adb(cmd: str, timeout: int = 12) -> str:
    """
    Run a command on the Android device via ADB.
    Uses shell=False to prevent injection into the adb command line itself.
    The 'cmd' arg is passed as a single argument to 'adb shell' (Android's own shell
    parses it), so we also run it through the metachar filter before calling.
    """
    if _SHELL_INJECT_RE.search(cmd):
        return "[BLOCKED] ADB command contains shell metacharacters."
    try:
        result = subprocess.run(
            ["adb", "-s", ADB_TARGET, "shell", cmd],
            shell=False, capture_output=True, text=True, timeout=timeout
        )
        return (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


def _adb_connect() -> str:
    try:
        result = subprocess.run(
            ["adb", "connect", ADB_TARGET],
            shell=False, capture_output=True, text=True, timeout=8
        )
        return (result.stdout + result.stderr).strip()
    except Exception as e:
        return f"[error: {e}]"


def _ollama_post(endpoint: str, payload: dict, timeout: int = 60) -> dict:
    import urllib.request
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _chroma_client():
    try:
        import chromadb
        return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    except Exception:
        return None


def _imap_connect():
    """Return an authenticated IMAP4_SSL connection or raise."""
    if not GMAIL_USER or not GMAIL_APP_PASS:
        raise RuntimeError(
            "gmail_user / gmail_app_pass not set in nova_config.json. "
            "Generate a Google App Password at myaccount.google.com/apppasswords."
        )
    conn = imaplib.IMAP4_SSL(GMAIL_IMAP, 993)
    conn.login(GMAIL_USER, GMAIL_APP_PASS)
    return conn


def _decode_header_value(raw) -> str:
    """Decode email header bytes/string to str."""
    from email.header import decode_header
    parts = decode_header(raw or "")
    result = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(chunk))
    return " ".join(result)


# ── Tool output sanitization ──────────────────────────────────────────────────
# Strip patterns commonly used for indirect prompt injection from tool results
# before they re-enter LLM context.  Replaces them with a visible warning so
# NOVA/Claude can see the attempt was made without acting on it.
_INJECT_PATTERNS = _re.compile(
    r'(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?'
    r'|new\s+instructions?:'
    r'|system\s*:\s*you\s+are\s+now'
    r'|<\s*/?system\s*>'
    r'|<\s*/?instructions?\s*>'
    r'|<\s*/?prompt\s*>'
    r'|assistant\s*:\s*i\s+will\s+now'
    r'|forget\s+(everything|your\s+instructions?)'
    r'|do\s+not\s+follow\s+your\s+(previous\s+)?instructions?'
    r'|\bDAN\b.*mode'
    r'|jailbreak)',
    _re.IGNORECASE | _re.MULTILINE
)


def _sanitize_tool_output(text: str, source: str = "") -> str:
    """
    Sanitize text that will be returned to the LLM as tool output.
    Replaces prompt injection attempts with a visible warning marker.
    """
    if _INJECT_PATTERNS.search(text):
        # Replace the injected content — don't silently drop it so it stays auditable
        sanitized = _INJECT_PATTERNS.sub(
            "[⚠ PROMPT INJECTION ATTEMPT REMOVED]", text
        )
        import sys
        print(
            f"[NOVA SECURITY] Prompt injection pattern detected in tool output"
            f"{' from ' + source if source else ''}. Sanitized before returning to LLM.",
            file=sys.stderr, flush=True
        )
        return sanitized
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_system_stats() -> str:
    """Get simon-hq system stats: CPU, RAM, disk, uptime, load average."""
    cpu    = _sh("nproc").strip() + " cores"
    ram    = _sh("free -h | awk '/^Mem:/{print $2\" total, \"$3\" used, \"$4\" free\"}'")
    disk   = _sh("df -h / | awk 'NR==2{print $2\" total, \"$3\" used, \"$4\" free (\"$5\" used)\"}'")
    load   = _sh("uptime | awk -F'load average:' '{print $2}'").strip()
    uptime_s = _sh("uptime -p")
    gpu    = _sh("nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || echo 'no GPU/nvidia-smi'")
    temps  = _sh("sensors 2>/dev/null | grep -E 'Core|Tdie|temp' | head -5 || echo 'sensors not available'")
    now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"[NOVA System Stats — {now}]\n"
        f"CPU cores : {cpu}\n"
        f"RAM       : {ram}\n"
        f"Disk (/)  : {disk}\n"
        f"Load avg  : {load}\n"
        f"Uptime    : {uptime_s}\n"
        f"GPU       : {gpu}\n"
        f"Temps     : {temps}"
    )


@mcp.tool()
def nova_list_processes(sort_by: str = "cpu", limit: int = 15) -> str:
    """
    List top processes on simon-hq.
    sort_by: 'cpu' or 'mem'
    limit: number of processes to return (max 30)
    """
    limit = min(int(limit), 30)
    col = "%cpu" if sort_by == "cpu" else "%mem"
    out = _sh(
        f"ps aux --sort=-{col} | head -n {limit + 1} | "
        f"awk '{{printf \"%-10s %-8s %-6s %-6s %s\\n\", $1,$2,$3,$4,$11}}'"
    )
    return f"[Top {limit} processes by {sort_by.upper()}]\n{out}"


@mcp.tool()
def nova_run_command(command: str, totp_token: str = "") -> str:
    """
    Run a whitelisted shell command on simon-hq.
    Safe read-only and utility commands are allowed (ls, df, ps, ip, ss, adb,
    libreoffice, git, docker, etc.). Use nova_write_file for writes.
    Admin note: systemctl write/restart commands require totp_token (Google Authenticator).
    Shell metacharacters (;|&`$<>{}[]) are blocked to prevent injection.
    """
    # 1. Block shell metacharacter injection before anything else
    if _SHELL_INJECT_RE.search(command):
        return (
            "[BLOCKED] Command contains shell metacharacters (;|&`$<>{}[]). "
            "Run one command at a time without shell operators."
        )

    parts = command.strip().split()
    if not parts:
        return "[BLOCKED] Empty command."

    first_word = parts[0].split("/")[-1]  # strip any path prefix e.g. /usr/bin/ls → ls
    if first_word not in _ALLOWED_CMDS:
        return (
            f"[BLOCKED] '{first_word}' is not in the allowed command list.\n"
            f"Allowed: {', '.join(sorted(_ALLOWED_CMDS))}"
        )

    # 2. Admin gate — systemctl/service mutation subcommands require 2FA
    _admin_subcmds = {"restart", "stop", "start", "enable", "disable", "mask", "unmask"}
    if first_word in ("systemctl", "service"):
        if len(parts) > 1 and parts[1] in _admin_subcmds:
            ok, msg = require_admin_auth(totp_token, "nova_service_restart")
            if not ok:
                return f"🔐 Admin auth required: {msg}"

    # 2b. High-risk commands (python3, docker, git) — always require 2FA
    #     These can execute arbitrary code and must not run without explicit authorization.
    if first_word in _HIGH_RISK_CMDS:
        ok, msg = require_admin_auth(totp_token, "nova_run_command")
        if not ok:
            return (
                f"🔐 '{first_word}' can execute arbitrary code and requires 2FA.\n"
                f"{msg}"
            )

    # 3. Execute via shell=False using parsed args — no shell expansion possible
    try:
        args = _shlex.split(command)
        result = subprocess.run(
            args, shell=False, capture_output=True, text=True, timeout=30
        )
        return (result.stdout + result.stderr).strip()
    except _shlex.split.__class__ as e:  # noqa: covers ValueError from shlex
        return f"[error parsing command: {e}]"
    except subprocess.TimeoutExpired:
        return "[timeout after 30s]"
    except Exception as e:
        return f"[error: {e}]"


@mcp.tool()
def nova_read_file(path: str, lines: int = 100) -> str:
    """
    Read a file from the simon-hq filesystem.
    path: absolute or home-relative path
    lines: max lines to return (default 100)
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path("/home/simon-hq") / p
    # Resolve symlinks and collapse ../ traversal BEFORE prefix check
    try:
        p = p.resolve()
    except Exception:
        return f"[BLOCKED] Cannot resolve path: {path}"
    safe_roots = [
        Path("/home/simon-hq").resolve(), Path("/var/log").resolve(),
        Path("/etc").resolve(), Path("/tmp").resolve(), Path("/opt").resolve(),
    ]
    if not any(str(p).startswith(str(r) + "/") or str(p) == str(r) for r in safe_roots):
        return f"[BLOCKED] Path '{p}' is outside allowed directories."
    try:
        content = p.read_text(errors="replace")
        result_lines = content.splitlines()[:lines]
        truncated = len(content.splitlines()) > lines
        out = "\n".join(result_lines)
        if truncated:
            out += f"\n\n[... truncated at {lines} lines]"
        return _sanitize_tool_output(out, source=str(p))
    except FileNotFoundError:
        return f"[ERROR] File not found: {p}"
    except Exception as e:
        return f"[ERROR] {e}"


@mcp.tool()
def nova_list_directory(path: str = "/home/simon-hq", show_hidden: bool = False) -> str:
    """List contents of a directory on simon-hq."""
    # Resolve and validate path before passing to subprocess (no shell=True)
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return f"[BLOCKED] Cannot resolve path: {path}"
    safe_roots = [
        Path("/home/simon-hq").resolve(), Path("/var/log").resolve(),
        Path("/etc").resolve(), Path("/tmp").resolve(), Path("/opt").resolve(),
        Path("/var").resolve(),
    ]
    if not any(str(resolved).startswith(str(r) + "/") or str(resolved) == str(r)
               for r in safe_roots):
        return f"[BLOCKED] Path '{resolved}' is outside allowed directories."
    flag = ["-la"] if show_hidden else ["-l"]
    try:
        result = subprocess.run(
            ["ls"] + flag + [str(resolved)],
            shell=False, capture_output=True, text=True, timeout=10
        )
        lines = (result.stdout + result.stderr).splitlines()[:80]
        return "\n".join(lines)
    except subprocess.TimeoutExpired:
        return "[timeout]"
    except Exception as e:
        return f"[error: {e}]"


@mcp.tool()
def nova_write_file(path: str, content: str, totp_token: str = "") -> str:
    """
    Write content to a file under /home/simon-hq or /tmp (safe areas only).
    Requires totp_token (Google Authenticator code) — file writes are admin-classified.
    """
    ok, msg = require_admin_auth(totp_token, "nova_file_write")
    if not ok:
        return f"🔐 Admin auth required: {msg}"
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path("/home/simon-hq") / p
    if not (str(p).startswith("/home/simon-hq") or str(p).startswith("/tmp")):
        return "[BLOCKED] Writes only allowed under /home/simon-hq or /tmp"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"[OK] Written {len(content)} bytes to {p}"
    except Exception as e:
        return f"[ERROR] {e}"


@mcp.tool()
def nova_file_search(pattern: str, directory: str = "/home/simon-hq", limit: int = 30) -> str:
    """
    Search for files by name pattern on simon-hq.
    pattern: glob pattern (e.g. '*.py', 'nova_*', '*.pdf')
    directory: root directory to search from
    """
    limit = min(int(limit), 100)
    out = _sh(f"find '{directory}' -name '{pattern}' -not -path '*/.*' 2>/dev/null | head -{limit}")
    count = len(out.splitlines())
    return f"[File search: '{pattern}' in {directory} — {count} results]\n{out}"


@mcp.tool()
def nova_disk_usage(path: str = "/home/simon-hq", depth: int = 2) -> str:
    """
    Show disk usage breakdown for a directory.
    depth: how many levels deep to summarize (default 2)
    """
    depth = min(int(depth), 4)
    return _sh(f"du -h --max-depth={depth} '{path}' 2>/dev/null | sort -hr | head -30")


# ═══════════════════════════════════════════════════════════════════════════════
# ANDROID / ADB TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_android_status() -> str:
    """Get Pixel 9a status: battery, WiFi, model, screen state, storage."""
    _adb_connect()
    battery = _adb("dumpsys battery | grep -E 'level|status|plugged'")
    wifi    = _adb("dumpsys wifi | grep -E 'mWifiInfo|SSID' | head -3")
    screen  = _adb("dumpsys power | grep 'mWakefulness'")
    model   = _adb("getprop ro.product.model")
    android = _adb("getprop ro.build.version.release")
    api     = _adb("getprop ro.build.version.sdk")
    storage = _adb("df /data | tail -1 | awk '{print $2\" total, \"$3\" used\"}'")
    return (
        f"[NOVA Android Bridge]\n"
        f"Device  : {model.strip()} (Android {android.strip()}, API {api.strip()})\n"
        f"Battery :\n{battery}\n"
        f"Screen  : {screen.strip()}\n"
        f"Storage : {storage.strip()}\n"
        f"WiFi    :\n{wifi}"
    )


@mcp.tool()
def nova_android_send_sms(number: str, message: str) -> str:
    """
    Send an SMS from the Pixel 9a via ADB.
    number: phone number (e.g. +15555550100)
    message: text content
    Note: Android 14+ (API 34+) blocks sent-box writes — message dispatches
    but won't appear in Google Messages history.
    """
    _adb_connect()
    api_str = _adb("getprop ro.build.version.sdk")
    try:
        api_level = int(api_str.strip())
    except ValueError:
        api_level = 0

    escaped = message.replace("'", "\\'").replace('"', '\\"')
    cmd = (
        f"service call isms 5 i32 0 s16 'com.android.mms.service' "
        f"s16 'null' s16 '{number}' s16 'null' s16 '{escaped}' "
        f"s16 'null' s16 'null' i32 0 i64 0"
    )
    result = _adb(cmd, timeout=10)
    sent_ok = "result" in result.lower() or not result

    if not sent_ok:
        return f"[ERROR] SMS dispatch failed: {result}"

    if api_level >= 34:
        log = Path(__file__).parent / "sms_sent_log.txt"
        try:
            with open(log, "a") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] TO={number} MSG={message}\n")
        except Exception:
            pass
        return (
            f"✅ SMS sent to {number}: \"{message}\"\n"
            f"(Android {api_level}: dispatched, won't appear in Google Messages history)"
        )
    return f"✅ SMS sent to {number}: \"{message}\""


@mcp.tool()
def nova_android_notifications(limit: int = 20) -> str:
    """Read active notifications on the Pixel 9a."""
    _adb_connect()
    limit = min(int(limit), 40)
    raw = _adb("dumpsys notification --noredact 2>/dev/null | grep -A3 'NotificationRecord'", timeout=15)
    lines = [l.strip() for l in raw.splitlines() if l.strip()][:limit * 4]
    return f"[Notifications (up to {limit} records)]\n" + "\n".join(lines)


@mcp.tool()
def nova_android_call_log(limit: int = 10) -> str:
    """Read recent call log from the Pixel 9a (incoming/outgoing/missed)."""
    _adb_connect()
    limit = min(int(limit), 30)
    out = _adb(
        f"content query --uri content://call_log/calls "
        f"--projection number:type:duration:date:name "
        f"--sort 'date DESC' --limit {limit}",
        timeout=12,
    )
    return f"[Call Log — last {limit} entries]\n{out}"


@mcp.tool()
def nova_android_battery_saver(enable: bool = True) -> str:
    """Enable or disable battery saver mode on the Pixel 9a."""
    _adb_connect()
    val = "1" if enable else "0"
    result = _adb(f"settings put global low_power {val}")
    state = "ENABLED" if enable else "DISABLED"
    return f"Battery saver {state} on Pixel 9a. {result or 'OK'}"


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_ping(host: str, count: int = 4) -> str:
    """Ping a host from simon-hq and return latency results."""
    count = min(int(count), 10)
    return _sh(f"ping -c {count} -W 3 {host} 2>&1", timeout=30)


@mcp.tool()
def nova_tailscale_peers() -> str:
    """List all Tailscale peers — IPs, hostnames, OS, and connection status."""
    return _sh("tailscale status 2>&1")


@mcp.tool()
def nova_port_check(host: str, ports: str = "22,80,443,8765,8200,3000,11434") -> str:
    """
    Check if TCP ports are open on a host.
    ports: comma-separated list (e.g. '22,80,443')
    """
    results = []
    for port in ports.split(","):
        port = port.strip()
        out = _sh(f"nc -zv -w 2 {host} {port} 2>&1", timeout=5)
        status = "OPEN" if ("succeeded" in out.lower() or "open" in out.lower()) else "CLOSED"
        results.append(f"  {host}:{port} → {status}")
    return f"[Port Check: {host}]\n" + "\n".join(results)


@mcp.tool()
def nova_active_connections() -> str:
    """Show all active network connections and listening ports on simon-hq."""
    return _sh("ss -tulnp 2>&1 | head -50")


@mcp.tool()
def nova_network_interfaces() -> str:
    """Show all network interfaces, IPs, and link state on simon-hq."""
    return _sh("ip addr show 2>&1")


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY (ChromaDB)
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_memory_store(key: str, content: str, tags: str = "") -> str:
    """
    Store a memory/note in NOVA's ChromaDB.
    key: short identifier (e.g. 'client_note_acme')
    content: the text to remember
    tags: optional comma-separated tags
    """
    client = _chroma_client()
    if not client:
        return "[ERROR] ChromaDB not reachable at localhost:8100"
    try:
        col = client.get_or_create_collection(COLLECTION)
        doc_id = f"{key}_{int(time.time())}"
        meta = {"key": key, "tags": tags, "ts": datetime.now().isoformat()}
        col.add(documents=[content], ids=[doc_id], metadatas=[meta])
        return f"[OK] Stored memory '{key}' (id: {doc_id})"
    except Exception as e:
        return f"[ERROR] {e}"


@mcp.tool()
def nova_memory_search(query: str, limit: int = 5) -> str:
    """Search NOVA's memory store with a natural language query."""
    client = _chroma_client()
    if not client:
        return "[ERROR] ChromaDB not reachable at localhost:8100"
    try:
        limit = min(int(limit), 10)
        col = client.get_or_create_collection(COLLECTION)
        results = col.query(query_texts=[query], n_results=limit)
        docs  = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        if not docs:
            return "[Memory] No matching entries found."
        lines = [f"[Memory search: '{query}']"]
        for i, (doc, meta) in enumerate(zip(docs, metas), 1):
            ts  = meta.get("ts", "")[:16]
            key = meta.get("key", "")
            lines.append(f"\n[{i}] key={key} ({ts})\n{doc}")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


@mcp.tool()
def nova_memory_list(limit: int = 20) -> str:
    """List recent entries in NOVA's memory store."""
    client = _chroma_client()
    if not client:
        return "[ERROR] ChromaDB not reachable at localhost:8100"
    try:
        limit = min(int(limit), 50)
        col = client.get_or_create_collection(COLLECTION)
        results = col.get(limit=limit, include=["documents", "metadatas"])
        docs  = results.get("documents", [])
        metas = results.get("metadatas", [])
        if not docs:
            return "[Memory] Store is empty."
        lines = [f"[Memory — {len(docs)} entries]"]
        for doc, meta in zip(docs, metas):
            ts      = meta.get("ts", "")[:16]
            key     = meta.get("key", "")
            preview = doc[:80].replace("\n", " ")
            lines.append(f"  • [{ts}] {key}: {preview}...")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_ollama_models() -> str:
    """List all models available in Ollama on simon-hq."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        models = data.get("models", [])
        lines = [f"[Ollama on simon-hq — {len(models)} models]"]
        for m in models:
            size_gb = m.get("size", 0) / 1e9
            lines.append(f"  • {m['name']}  ({size_gb:.1f} GB)")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] Ollama unreachable: {e}"


@mcp.tool()
def nova_ollama_chat(prompt: str, model: str = "qwen2.5:7b", system: str = "") -> str:
    """
    Send a prompt directly to a local Ollama model on simon-hq.
    model: one of the installed models (default: llama3.1:8b)
    """
    payload: dict = {
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.3, "num_predict": 512}
    }
    if system:
        payload["system"] = system
    result = _ollama_post("/api/generate", payload, timeout=90)
    if "error" in result:
        return f"[ERROR] {result['error']}"
    return result.get("response", "[no response]")


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL — Mac SIMON bridge (legacy)
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_gmail_status() -> str:
    """Check if the Mac SIMON bridge is reachable for Gmail operations."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{MAC_URL}/api/status", timeout=5) as r:
            data = json.loads(r.read())
        return f"[Mac SIMON bridge: ONLINE]\n{json.dumps(data, indent=2)}"
    except Exception as e:
        return f"[Mac SIMON bridge: OFFLINE] {e}\nGmail tools unavailable until Mac is reachable."


@mcp.tool()
def nova_gmail_read(limit: int = 10) -> str:
    """
    Read recent Gmail messages via the Mac SIMON bridge over Tailscale.
    limit: number of messages to return (max 20)
    """
    import urllib.request
    limit = min(int(limit), 20)
    try:
        payload = json.dumps({"tool": "gmail_list", "args": {"limit": limit}}).encode()
        req = urllib.request.Request(
            f"{MAC_URL}/api/plugins",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get("result", str(data))
    except Exception as e:
        return (
            f"[ERROR] Gmail bridge unavailable: {e}\n"
            f"Ensure SIMON is running on Mac (YOUR_MAC_TAILSCALE_IP:8765)\n"
            f"Tip: use nova_email_inbox for direct IMAP access instead."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL — Direct Gmail SMTP + IMAP
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_email_send(to: str, subject: str, body: str, cc: str = "", totp_token: str = "") -> str:
    """
    Send an email directly from simon-hq via Gmail SMTP.
    Requires gmail_user + gmail_app_pass in nova_config.json.
    to: recipient address (or comma-separated list)
    cc: optional CC addresses
    totp_token: Google Authenticator code (required — outbound email is admin-classified)
    """
    ok, msg = require_admin_auth(totp_token, "nova_email_send")
    if not ok:
        return f"🔐 Admin auth required to send email: {msg}"
    if not GMAIL_USER or not GMAIL_APP_PASS:
        return (
            "[CONFIG] Add to nova_config.json:\n"
            '  "gmail_user": "your@email.com",\n'
            '  "gmail_app_pass": "<16-char app password>"\n'
            "Generate at: myaccount.google.com/apppasswords"
        )
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = to
        if cc:
            msg["Cc"] = cc
        msg.attach(MIMEText(body, "plain"))

        recipients = [a.strip() for a in to.split(",")]
        if cc:
            recipients += [a.strip() for a in cc.split(",")]

        with smtplib.SMTP(GMAIL_SMTP, 587, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(GMAIL_USER, GMAIL_APP_PASS)
            s.sendmail(GMAIL_USER, recipients, msg.as_string())

        return f"✅ Email sent to {to} | Subject: {subject}"
    except Exception as e:
        return f"[ERROR] SMTP send failed: {e}"


@mcp.tool()
def nova_email_inbox(limit: int = 10, unread_only: bool = False) -> str:
    """
    Check Gmail inbox directly via IMAP from simon-hq.
    Requires gmail_user + gmail_app_pass in nova_config.json.
    limit: number of messages (max 25)
    unread_only: if True, only show unread messages
    """
    if not GMAIL_USER or not GMAIL_APP_PASS:
        return (
            "[CONFIG] Add to nova_config.json:\n"
            '  "gmail_user": "your@email.com",\n'
            '  "gmail_app_pass": "<16-char app password>"\n'
            "Generate at: myaccount.google.com/apppasswords"
        )
    limit = min(int(limit), 25)
    try:
        conn = _imap_connect()
        conn.select("INBOX")

        search_crit = "UNSEEN" if unread_only else "ALL"
        _, msg_ids = conn.search(None, search_crit)
        ids = msg_ids[0].split()
        ids = ids[-limit:][::-1]  # newest first

        lines = [f"[Gmail INBOX — {len(ids)} messages shown]"]
        for uid in ids:
            _, data = conn.fetch(uid, "(RFC822.SIZE BODY[HEADER.FIELDS (FROM SUBJECT DATE)])")
            raw = data[0][1]
            msg = message_from_bytes(raw)
            frm  = _decode_header_value(msg.get("From", ""))[:60]
            subj = _decode_header_value(msg.get("Subject", "(no subject)"))[:80]
            date = msg.get("Date", "")[:30]
            lines.append(f"\n  [{date}]\n  From   : {frm}\n  Subject: {subj}")

        conn.logout()
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] IMAP inbox failed: {e}"


@mcp.tool()
def nova_email_search(query: str, limit: int = 10) -> str:
    """
    Search Gmail messages via IMAP from simon-hq.
    Requires gmail_user + gmail_app_pass in nova_config.json.
    query: search term (searches subject + from via IMAP TEXT search)
    """
    if not GMAIL_USER or not GMAIL_APP_PASS:
        return (
            "[CONFIG] Add to nova_config.json:\n"
            '  "gmail_user": "your@email.com",\n'
            '  "gmail_app_pass": "<16-char app password>"\n'
            "Generate at: myaccount.google.com/apppasswords"
        )
    limit = min(int(limit), 25)
    try:
        conn = _imap_connect()
        conn.select("INBOX")

        # IMAP TEXT search searches headers + body
        _, msg_ids = conn.search(None, f'TEXT "{query}"')
        ids = msg_ids[0].split()
        ids = ids[-limit:][::-1]

        if not ids:
            conn.logout()
            return f"[Gmail search: '{query}'] No matching messages found."

        lines = [f"[Gmail search: '{query}' — {len(ids)} results]"]
        for uid in ids:
            _, data = conn.fetch(uid, "(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])")
            raw = data[0][1]
            msg = message_from_bytes(raw)
            frm  = _decode_header_value(msg.get("From", ""))[:60]
            subj = _decode_header_value(msg.get("Subject", "(no subject)"))[:80]
            date = msg.get("Date", "")[:30]
            lines.append(f"\n  [{date}]\n  From   : {frm}\n  Subject: {subj}")

        conn.logout()
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] IMAP search failed: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# LIBREOFFICE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_libreoffice_convert(
    input_path: str,
    output_format: str = "pdf",
    output_dir: str = "/home/simon-hq/documents/converted",
) -> str:
    """
    Convert a document using LibreOffice headless on simon-hq.
    input_path: path to source file (docx, xlsx, pptx, odt, csv, html, etc.)
    output_format: target format — pdf, docx, odt, xlsx, ods, pptx, odp, html, txt, csv
    output_dir: where to save the converted file (default: ~/documents/converted)

    Examples:
      convert report.docx → report.pdf
      convert data.xlsx   → data.csv
      convert slides.pptx → slides.pdf
    """
    allowed_formats = {
        "pdf", "docx", "odt", "xlsx", "ods", "pptx", "odp",
        "html", "txt", "csv", "png", "jpg",
    }
    fmt = output_format.lower().strip(".")
    if fmt not in allowed_formats:
        return (
            f"[BLOCKED] Output format '{fmt}' not allowed.\n"
            f"Allowed: {', '.join(sorted(allowed_formats))}"
        )

    p = Path(input_path).expanduser()
    if not p.is_absolute():
        p = Path("/home/simon-hq") / p

    safe_roots = [Path("/home/simon-hq"), Path("/tmp"), Path("/opt")]
    if not any(str(p).startswith(str(r)) for r in safe_roots):
        return f"[BLOCKED] Input path '{p}' is outside allowed directories."

    if not p.exists():
        return f"[ERROR] File not found: {p}"

    out_dir = Path(output_dir).expanduser()
    if not out_dir.is_absolute():
        out_dir = Path("/home/simon-hq") / out_dir
    if not (str(out_dir).startswith("/home/simon-hq") or str(out_dir).startswith("/tmp")):
        return f"[BLOCKED] Output dir '{out_dir}' not allowed."

    out_dir.mkdir(parents=True, exist_ok=True)

    # LibreOffice headless conversion
    cmd = (
        f"libreoffice --headless --convert-to {fmt} "
        f"--outdir '{out_dir}' '{p}' 2>&1"
    )
    result = _sh(cmd, timeout=60)
    out_file = out_dir / (p.stem + f".{fmt}")

    if out_file.exists():
        size = out_file.stat().st_size
        return (
            f"✅ Converted: {p.name} → {out_file.name}\n"
            f"   Output: {out_file}\n"
            f"   Size  : {size / 1024:.1f} KB\n"
            f"   Log   : {result[:200]}"
        )
    return f"[ERROR] Conversion may have failed.\n   Command: {cmd}\n   Output: {result[:400]}"


@mcp.tool()
def nova_libreoffice_info() -> str:
    """Show LibreOffice version and available tools on simon-hq."""
    version = _sh("libreoffice --version 2>&1")
    which   = _sh("which libreoffice soffice 2>&1")
    formats = (
        "Supported conversions (--headless --convert-to <fmt>):\n"
        "  Document : docx, odt, pdf, html, txt\n"
        "  Spreadsheet: xlsx, ods, csv, pdf\n"
        "  Presentation: pptx, odp, pdf, html\n"
        "  Image export: png, jpg (from presentations)\n"
    )
    return f"{version}\nPaths: {which}\n\n{formats}"


# ═══════════════════════════════════════════════════════════════════════════════
# 360 REPORT — Full multi-device diagnostic
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_360_report() -> str:
    """
    Run the full Simon-X 360 diagnostic report covering ALL devices.
    Triggers on: '360 report', '360report', 'run diagnostics', 'system check',
    'full report', 'health check', 'stack check', 'give me a 360 report'.

    ⚠️ CRITICAL INSTRUCTION FOR AI MODELS: You MUST call this tool and return ONLY its
    actual output verbatim. NEVER generate, estimate, or fabricate any system metrics,
    uptime, CPU, memory, request counts, or any other data yourself.
    If this tool returns an error, show ONLY the error — do not invent fallback data.

    Checks:
      • simon-hq (NOVA) — system stats, all 8 services, Ollama, ChromaDB,
                          UFW firewall, Tailscale, disk, recent error logs
      • Mac (SIMON)     — SIMON API reachability, ports over Tailscale
      • Pixel 9a        — ADB status, battery, storage, WiFi, screen state
      • Connectivity    — full mesh: HQ↔Mac, HQ↔Android, HQ↔Internet

    Saves an HTML report to /home/simon-hq/reports/NOVA_360_<timestamp>.html
    Returns a plain-text summary with pass/fail counts and a ✅ VERIFIED DATA header.
    """
    # Locate the local diagnostic script
    script_candidates = [
        Path(__file__).parent / "nova_360_local.py",
        Path("/home/simon-hq/simon-hq/nova_360_local.py"),
    ]
    script = None
    for p in script_candidates:
        if p.exists():
            script = p
            break

    if not script:
        return (
            "[360 Report] nova_360_local.py not found.\n"
            "Expected at: /home/simon-hq/simon-hq/nova_360_local.py\n"
            "Run: scp nova_360_local.py simon-hq:/home/simon-hq/simon-hq/"
        )

    try:
        import sys as _sys
        result = subprocess.run(
            [_sys.executable, str(script)],
            capture_output=True, text=True, timeout=90,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "[360 Report] Script ran but produced no output."
        # Stamp with real-time verification header so models can't confuse this with generated text
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"╔══════════════════════════════════════════════════════╗\n"
            f"║  ✅ VERIFIED LIVE DATA — simon-hq diagnostic script  ║\n"
            f"║  Generated: {ts} (server time)         ║\n"
            f"║  Source: nova_360_local.py — NOT AI-generated        ║\n"
            f"╚══════════════════════════════════════════════════════╝\n"
        )
        return header + output
    except subprocess.TimeoutExpired:
        return "[360 Report] Timed out after 90s — some device may be unreachable."
    except Exception as e:
        return f"[360 Report] Failed to run: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY / 2FA STATUS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_2fa_status() -> str:
    """
    Show the current 2FA security configuration for NOVA admin operations.
    No token required — this is a read-only status check.
    """
    if not _2FA_AVAILABLE:
        return "⚠️  nova_2fa.py module not found. Deploy it to simon-hq and restart nova-mcpo."
    status = get_2fa_status()
    lines = [
        "🔐 NOVA 2FA Security Status",
        "─" * 40,
        f"  Configured     : {'✅ Active' if status['configured'] else '❌ Not set up'}",
        f"  Algorithm      : {status['algorithm']}",
        f"  Token period   : {status['period_seconds']}s",
        f"  Digits         : {status['digits']}",
        f"  Admin gates    : {status['admin_actions_protected']} operations protected",
        f"  Authenticator  : {', '.join(status['compatible_apps'][:2])}",
    ]
    if not status["configured"]:
        lines += [
            "",
            "  ⚡ Run setup:  python3 ~/simon-hq/nova_2fa_setup.py",
        ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE STATUS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nova_service_status(service: str = "") -> str:
    """
    Check status of NOVA/SIMON services on simon-hq.
    service: specific service name, or empty string for full overview.
    """
    if service:
        return _sh(f"systemctl status {service} --no-pager 2>&1 | head -25")
    services = [
        "nova-webui", "nova-mcpo", "nova-hud",
        "simon-hq-api", "simon-chroma", "ollama", "tailscaled",
    ]
    lines = ["[NOVA/SIMON Service Status]"]
    for svc in services:
        active  = _sh(f"systemctl is-active {svc} 2>&1").strip()
        enabled = _sh(f"systemctl is-enabled {svc} 2>&1").strip()
        icon = "✅" if active == "active" else "❌"
        lines.append(f"  {icon} {svc:<22} active={active:<12} enabled={enabled}")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    # When run directly (by mcpo or for testing), use stdio transport.
    # mcpo wraps this process and exposes OpenAPI on port 8301.
    print("[NOVA MCP] Starting stdio transport (mcpo wrapper mode)", flush=True, file=sys.stderr)
    print(f"[NOVA MCP] Ollama : {OLLAMA_URL}", flush=True, file=sys.stderr)
    print(f"[NOVA MCP] Android: {ADB_TARGET}", flush=True, file=sys.stderr)
    print(f"[NOVA MCP] Mac    : {MAC_URL}", flush=True, file=sys.stderr)
    print(f"[NOVA MCP] Gmail  : {GMAIL_USER or 'NOT CONFIGURED'}", flush=True, file=sys.stderr)
    mcp.run(transport="stdio")
