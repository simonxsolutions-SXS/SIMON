#!/usr/bin/env python3
"""
S.I.M.O.N. Plugin — 360 Report
================================
Keyword trigger: "360 report" | "360report" | "run diagnostics" | "system check"

Runs the full NOVA/SIMON multi-device diagnostic against Mac, simon-hq,
and the Pixel 9a. Saves a styled HTML report to ~/Desktop and returns
a plain-text summary to the chat.
"""

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Plugin metadata ───────────────────────────────────────────────────────────
METADATA = {
    "name":        "360 Report",
    "description": "Full system diagnostic — Mac, simon-hq, Pixel 9a",
    "version":     "1.0",
    "author":      "Simon-X Solutions",
    "keywords":    ["360 report", "360report", "run diagnostics", "system check",
                    "full report", "stack check", "health check"],
}

# ── Tool definition ───────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_360_report",
            "description": (
                "Run the full Simon-X 360 diagnostic report. "
                "Triggers on: '360 report', '360report', 'run diagnostics', "
                "'system check', 'full report', 'health check', 'stack check'. "
                "Checks Mac (SIMON), simon-hq (NOVA) and Pixel 9a — all services, "
                "ports, Ollama, ChromaDB, ADB/Android, Tailscale, firewall, and logs. "
                "Saves a styled HTML report to ~/Desktop and returns a text summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "open_browser": {
                        "type": "boolean",
                        "description": "Open the HTML report in the browser when done (default: true)"
                    }
                },
                "required": []
            }
        }
    }
]

# ── Execution ─────────────────────────────────────────────────────────────────

_SCRIPT = Path(__file__).parent.parent / "nova_system_report.py"


async def execute(name: str, args: dict):
    if name != "run_360_report":
        return None

    if not _SCRIPT.exists():
        return (
            f"[360 Report] Script not found at {_SCRIPT}.\n"
            "Expected nova_system_report.py in the jarvis folder."
        )

    open_browser = args.get("open_browser", True)

    ts_start = datetime.now().strftime("%H:%M:%S")
    try:
        # Run the report script, capture stdout for the summary
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        output = (stdout.decode(errors="replace") + stderr.decode(errors="replace")).strip()
        rc = proc.returncode
    except asyncio.TimeoutError:
        return "[360 Report] Timed out after 90s — simon-hq or Android may be unreachable."
    except Exception as e:
        return f"[360 Report] Failed to run script: {e}"

    # Find the saved file path in the output
    report_path = None
    for line in output.splitlines():
        if "Saved:" in line:
            report_path = line.split("Saved:")[-1].strip()
            break

    # Extract summary lines
    lines = output.splitlines()
    summary_lines = []
    capture = False
    for line in lines:
        if "REPORT COMPLETE" in line or "passing" in line or "ISSUES FOUND" in line:
            capture = True
        if capture:
            summary_lines.append(line)

    status_icon = "✅" if rc == 0 else "⚠️"
    summary = "\n".join(summary_lines[:25]).strip()

    report_link = f"\n📄 Report saved: {report_path}" if report_path else ""
    browser_note = "\n🌐 Opened in browser." if (open_browser and report_path and rc == 0) else ""

    return (
        f"{status_icon} **360 REPORT — {ts_start}**\n\n"
        f"{summary}"
        f"{report_link}"
        f"{browser_note}"
    )
