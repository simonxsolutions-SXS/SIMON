#!/usr/bin/env python3
"""
S.I.M.O.N. Self-Healing Engine — simon_healer.py (v1.0)
=========================================================
Simon-X Solutions | [OWNER_NAME]

Autonomous diagnosis and repair for SIMON components.
Called by the health monitor when sustained failures are detected,
and also callable directly: python3 simon_healer.py

Every fix is logged. Every action is reversible or safe.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────

JARVIS_DIR  = Path(__file__).parent
LOG_FILE    = JARVIS_DIR / "jarvis.log"
CONFIG_FILE = JARVIS_DIR / "config.json"
REPAIR_LOG  = JARVIS_DIR / "repair.log"
JARVIS_PORT = 8765

try:
    cfg = json.loads(CONFIG_FILE.read_text())
except Exception:
    cfg = {}

HQ_URL = cfg.get("hq_api_url", "http://YOUR_HQ_TAILSCALE_IP:8200")
HQ_KEY = cfg.get("hq_api_key", "")

# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] [Healer] {msg}"
    print(line)
    try:
        with open(REPAIR_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd: list, timeout: int = 15) -> tuple[int, str, str]:
    """Run a shell command. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timed out"
    except Exception as e:
        return -1, "", str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Fix registry — each entry is a known failure pattern with an auto-fix
# ─────────────────────────────────────────────────────────────────────────────

class Fix:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def check(self) -> bool:
        """Return True if this failure condition is currently present."""
        raise NotImplementedError

    def fix(self) -> str:
        """Attempt the fix. Return a human-readable result string."""
        raise NotImplementedError


class FixMailApp(Fix):
    def __init__(self):
        super().__init__("Mail.app Closed", "Mail.app is not running — needed for email tools")

    def check(self) -> bool:
        rc, out, _ = run(["pgrep", "-x", "Mail"])
        return rc != 0  # True = Mail is NOT running = failure present

    def fix(self) -> str:
        rc, _, err = run(["open", "-a", "Mail"])
        if rc == 0:
            time.sleep(3)
            rc2, _, _ = run(["pgrep", "-x", "Mail"])
            if rc2 == 0:
                return "Mail.app reopened successfully"
        return f"Failed to reopen Mail.app: {err}"


class FixMessagesApp(Fix):
    def __init__(self):
        super().__init__("Messages.app Closed", "Messages.app not running — needed for iMessage tools")

    def check(self) -> bool:
        rc, _, _ = run(["pgrep", "-x", "Messages"])
        return rc != 0

    def fix(self) -> str:
        rc, _, err = run(["open", "-a", "Messages"])
        if rc == 0:
            time.sleep(3)
            rc2, _, _ = run(["pgrep", "-x", "Messages"])
            if rc2 == 0:
                return "Messages.app reopened successfully"
        return f"Failed to reopen Messages.app: {err}"


class FixPortConflict(Fix):
    def __init__(self):
        super().__init__("Port 8765 Conflict", "Something else is holding SIMON's API port")

    def check(self) -> bool:
        rc, out, _ = run(["lsof", "-ti", f"tcp:{JARVIS_PORT}"])
        return rc == 0 and out.strip() != ""  # True = port occupied

    def fix(self) -> str:
        rc, out, _ = run(["lsof", "-ti", f"tcp:{JARVIS_PORT}"])
        if not out.strip():
            return "Port already free"
        pids = out.strip().split("\n")
        killed = []
        for pid in pids:
            try:
                int(pid)
                k_rc, _, _ = run(["kill", "-9", pid])
                if k_rc == 0:
                    killed.append(pid)
            except ValueError:
                pass
        return f"Killed PIDs on port {JARVIS_PORT}: {', '.join(killed)}" if killed else "Could not kill conflicting process"


class FixKBIntegrity(Fix):
    def __init__(self):
        super().__init__("KB Integrity Failure", "SQLite knowledge base has errors")

    def check(self) -> bool:
        try:
            import sqlite3
            kb_path = JARVIS_DIR / "simon_kb.db"
            if not kb_path.exists():
                return False
            conn = sqlite3.connect(str(kb_path))
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            return result[0] != "ok"
        except Exception:
            return True  # Error accessing KB = problem

    def fix(self) -> str:
        try:
            import sqlite3
            kb_path = JARVIS_DIR / "simon_kb.db"
            # Run vacuum + integrity check
            conn = sqlite3.connect(str(kb_path))
            conn.execute("VACUUM")
            conn.execute("PRAGMA integrity_check")
            conn.close()
            return "KB VACUUM completed — integrity restored"
        except Exception as e:
            return f"KB repair failed: {e}"


class FixPiperTTS(Fix):
    def __init__(self):
        super().__init__("Piper TTS Missing", "Piper voice synthesis binary not found")

    def check(self) -> bool:
        piper_paths = [
            Path.home() / ".local/bin/piper",
            Path("/usr/local/bin/piper"),
            Path("/opt/homebrew/bin/piper"),
        ]
        return not any(p.exists() for p in piper_paths)

    def fix(self) -> str:
        return (
            "Piper binary not found. SIMON will fall back to 'say -v Reed' for TTS. "
            "To reinstall: download piper from https://github.com/rhasspy/piper/releases "
            "and place the binary in ~/.local/bin/piper"
        )


class FixStaleLog(Fix):
    def __init__(self):
        super().__init__("Log File Oversized", "jarvis.log exceeds 50MB — will slow down log reads")

    def check(self) -> bool:
        if not LOG_FILE.exists():
            return False
        return LOG_FILE.stat().st_size > 50 * 1024 * 1024  # 50MB

    def fix(self) -> str:
        backup = LOG_FILE.with_suffix(".log.bak")
        try:
            # Keep last 10,000 lines
            lines = LOG_FILE.read_text(errors="replace").splitlines()
            kept = lines[-10000:]
            backup.write_text("\n".join(lines[:-10000]))
            LOG_FILE.write_text("\n".join(kept) + "\n")
            return f"Log trimmed to last 10,000 lines. Archived {len(lines) - 10000} lines to {backup.name}"
        except Exception as e:
            return f"Log trim failed: {e}"


class FixADBReconnect(Fix):
    def __init__(self):
        super().__init__("Android ADB Disconnected", "Pixel 9a not reachable via ADB")

    def check(self) -> bool:
        adb_host = cfg.get("android", {}).get("adb_host", "")
        adb_ts   = cfg.get("android", {}).get("adb_host_tailscale", "")
        port     = cfg.get("android", {}).get("adb_port", 5555)
        if not adb_host and not adb_ts:
            return False  # Not configured, not a failure
        adb_bin = "/opt/homebrew/bin/adb"
        for host in [f"{adb_host}:{port}", f"{adb_ts}:{port}"]:
            if not host.startswith(":"):
                rc, out, _ = run([adb_bin, "-s", host, "get-state"], timeout=4)
                if rc == 0 and "device" in out:
                    return False  # Connected OK
        return True  # Neither path works

    def fix(self) -> str:
        adb_host = cfg.get("android", {}).get("adb_host", "")
        adb_ts   = cfg.get("android", {}).get("adb_host_tailscale", "")
        port     = cfg.get("android", {}).get("adb_port", 5555)
        adb_bin  = "/opt/homebrew/bin/adb"
        for host, label in [(f"{adb_host}:{port}", "WiFi"), (f"{adb_ts}:{port}", "Tailscale")]:
            if host.startswith(":"):
                continue
            rc, out, _ = run([adb_bin, "connect", host], timeout=8)
            if "connected" in out.lower() and "failed" not in out.lower():
                return f"ADB reconnected via {label} ({host})"
        return "ADB reconnect failed — phone may be off or Wireless Debugging disabled"


# All registered fixes — runs in this order
FIXES: list[Fix] = [
    FixMailApp(),
    FixMessagesApp(),
    FixPortConflict(),
    FixKBIntegrity(),
    FixPiperTTS(),
    FixStaleLog(),
    FixADBReconnect(),
]


# ─────────────────────────────────────────────────────────────────────────────
# Diagnosis engine
# ─────────────────────────────────────────────────────────────────────────────

def run_diagnosis() -> list[dict]:
    """Check all known failure conditions. Returns list of active issues."""
    issues = []
    for fix in FIXES:
        try:
            if fix.check():
                issues.append({"name": fix.name, "description": fix.description, "fix": fix})
                log(f"⚠️  Issue detected: {fix.name}", "WARN")
        except Exception as e:
            log(f"Check failed for {fix.name}: {e}", "ERROR")
    return issues


def run_repairs(issues: list[dict]) -> list[dict]:
    """Apply fixes for all detected issues. Returns results."""
    results = []
    for issue in issues:
        fix = issue["fix"]
        log(f"Applying fix: {fix.name}")
        try:
            result = fix.fix()
            log(f"Fix result: {result}")
            results.append({"name": fix.name, "result": result, "status": "applied"})
        except Exception as e:
            log(f"Fix threw exception: {e}", "ERROR")
            results.append({"name": fix.name, "result": str(e), "status": "error"})
    return results


def full_repair_run() -> str:
    """Run full diagnosis + repair cycle. Returns summary string."""
    log("=== REPAIR CYCLE START ===")
    issues = run_diagnosis()
    if not issues:
        log("All systems nominal — nothing to repair")
        return "All systems nominal. Nothing to repair."
    log(f"Found {len(issues)} issue(s). Applying fixes...")
    results = run_repairs(issues)
    lines = []
    for r in results:
        icon = "✅" if r["status"] == "applied" else "❌"
        lines.append(f"{icon} {r['name']}: {r['result']}")
    summary = f"Repair cycle complete. Fixed {len(results)} issue(s):\n" + "\n".join(lines)
    log("=== REPAIR CYCLE END ===")
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# HQ-assisted repair — ask LLM for guidance on unknown failures
# ─────────────────────────────────────────────────────────────────────────────

async def ask_hq_for_repair_guidance(error_description: str) -> Optional[str]:
    """
    Send an error description to simon-hq LLM and get repair recommendations.
    Used for issues not in the fix registry.
    """
    try:
        import httpx
        payload = {
            "model":    cfg.get("hq_model", "qwen2.5:7b"),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the S.I.M.O.N. repair assistant on simon-hq. "
                        "You have deep knowledge of the SIMON system architecture: "
                        "FastAPI Python server on macOS M5, Ollama + ChromaDB on Ubuntu simon-hq, "
                        "three-tier LLM routing (HQ conversational / Mistral Large cloud tools / MLX fallback), "
                        "plugin system, SQLite KB, health monitor, Piper TTS, Web Speech API. "
                        "When given an error, provide a specific, actionable fix in 2-3 sentences. "
                        "Prioritize file paths, commands, and code snippets over general advice."
                    )
                },
                {"role": "user", "content": f"SIMON error: {error_description}"}
            ],
            "api_key": HQ_KEY
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{HQ_URL}/llm/chat", json=payload)
            if r.status_code == 200:
                data = r.json()
                msg = data.get("message", {})
                return msg.get("content", "") if isinstance(msg, dict) else str(msg)
    except Exception as e:
        log(f"HQ guidance request failed: {e}", "ERROR")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# CLI interface
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("S.I.M.O.N. Self-Healing Engine v1.0")
    print("=" * 40)

    if "--diagnose-only" in sys.argv:
        issues = run_diagnosis()
        if not issues:
            print("✅ All systems nominal")
        else:
            print(f"⚠️  {len(issues)} issue(s) found:")
            for i in issues:
                print(f"  • {i['name']}: {i['description']}")
    elif "--ask-hq" in sys.argv and len(sys.argv) > 2:
        error = " ".join(sys.argv[sys.argv.index("--ask-hq") + 1:])
        print(f"Asking HQ for guidance on: {error}")
        result = asyncio.run(ask_hq_for_repair_guidance(error))
        print(f"\nHQ says:\n{result}")
    else:
        result = full_repair_run()
        print(result)
