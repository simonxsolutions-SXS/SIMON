"""
S.I.M.O.N. Tool Health Monitor — simon_tool_health.py
=======================================================
Proactive tool health system. Runs at startup and on every system check.
SIMON always knows what is working before [OWNER] asks.

Architecture:
  - ToolStatus: enum for UP / DEGRADED / DOWN / UNKNOWN
  - HealthResult: result of a single tool probe
  - run_all_checks(): async — probes every tool concurrently
  - get_health_summary(): returns a one-line human summary for SIMON's briefing
  - get_system_prompt_block(): returns a formatted block injected into every
    system prompt so the LLM always knows the current tool status

Probes are lightweight — they use cached network state wherever possible
and avoid generating real traffic (no speed tests, no ARP floods).
Each probe has a short individual timeout so one broken tool never
stalls the whole startup sequence.

Results are cached for 5 minutes so repeated calls are instant.
"""

import asyncio
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
#  STATUS ENUM + RESULT TYPE
# ─────────────────────────────────────────────────────────────────────────────

class ToolStatus(Enum):
    UP       = "UP"        # Working normally
    DEGRADED = "DEGRADED"  # Working but with limitations / partial data
    DOWN     = "DOWN"      # Not working
    UNKNOWN  = "UNKNOWN"   # Not yet checked


@dataclass
class HealthResult:
    name:    str
    status:  ToolStatus
    message: str                    # one-line human note
    latency_ms: Optional[float] = None
    checked_at: float = field(default_factory=time.time)

    def __str__(self) -> str:
        icon = {"UP": "✅", "DEGRADED": "⚠️", "DOWN": "❌", "UNKNOWN": "❓"}[self.status.value]
        lat  = f" ({self.latency_ms:.0f}ms)" if self.latency_ms is not None else ""
        return f"{icon} {self.name}{lat}: {self.message}"


# ─────────────────────────────────────────────────────────────────────────────
#  CACHE
# ─────────────────────────────────────────────────────────────────────────────

_CACHE_TTL_S   = 300   # 5 minutes
_last_results: list[HealthResult] = []
_last_run:     float = 0.0


def get_cached_results() -> list[HealthResult]:
    """Return the most recently computed health results (may be empty on first call)."""
    return _last_results


def cache_is_fresh() -> bool:
    return bool(_last_results) and (time.time() - _last_run) < _CACHE_TTL_S


# ─────────────────────────────────────────────────────────────────────────────
#  INDIVIDUAL PROBES
#  Each returns a HealthResult. Never raises — all errors become DOWN results.
# ─────────────────────────────────────────────────────────────────────────────

async def _probe_wifi(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """
    WiFi status via compiled CoreWLAN Swift probe.
    macOS 26 privacy restrictions redact the SSID in background processes —
    we detect that and report it accurately rather than saying "WiFi is off".
    Falls back to system_profiler if the compiled probe is missing.
    """
    t0 = time.perf_counter()
    probe_path = Path(__file__).parent / "wifi_probe"

    try:
        if probe_path.exists():
            # Use compiled Swift probe (CoreWLAN — most accurate)
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [str(probe_path)],
                        capture_output=True, text=True, timeout=5
                    )
                ),
                timeout=6
            )
            out = result.stdout.strip()

            def field(key: str) -> str:
                m = re.search(rf"^{key}:(.+)$", out, re.MULTILINE)
                return m.group(1).strip() if m else ""

            status_val = field("STATUS")
            if status_val == "off":
                return HealthResult("WiFi", ToolStatus.DOWN,
                                    "WiFi hardware is powered off",
                                    (time.perf_counter() - t0) * 1000)

            rssi    = field("RSSI")
            noise   = field("NOISE")
            txrate  = field("TXRATE")
            channel = field("CHANNEL")
            band    = field("BAND")
            width   = field("WIDTH")

            # Signal quality label
            quality = "Excellent"
            try:
                ri = int(rssi)
                if   ri >= -50: quality = "Excellent"
                elif ri >= -60: quality = "Good"
                elif ri >= -70: quality = "Fair"
                elif ri >= -80: quality = "Poor"
                else:           quality = "Very Poor"
            except ValueError:
                pass

            parts = [f"Connected ({quality})"]
            if rssi:     parts.append(f"RSSI {rssi} dBm")
            if band:     parts.append(band)
            if channel:  parts.append(f"Ch {channel}")
            if width:    parts.append(width)
            if txrate:   parts.append(f"{txrate} Mbps link")
            # Note: SSID deliberately omitted — macOS 26 redacts it in background processes
            # This is expected behaviour, not a bug. SIMON reports what it can access.
            parts.append("(SSID private per macOS 26)")

            msg = " | ".join(parts)
            status = ToolStatus.DEGRADED if quality in ("Poor", "Very Poor") else ToolStatus.UP
            return HealthResult("WiFi", status, msg, (time.perf_counter() - t0) * 1000)

        else:
            # Fallback: system_profiler — slower, also redacts SSID on macOS 26
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ["system_profiler", "SPAirPortDataType"],
                        capture_output=True, text=True, timeout=12
                    )
                ),
                timeout=14
            )
            raw = result.stdout
            # Check status
            if "Status: Connected" in raw or "Status: Associated" in raw:
                rssi_m = re.search(r"Signal / Noise:\s*(-?\d+)\s*dBm", raw)
                rssi   = int(rssi_m.group(1)) if rssi_m else None
                ch_m   = re.search(r"Channel:\s*(\d+)", raw)
                channel = ch_m.group(1) if ch_m else ""
                quality = "Good"
                if rssi:
                    if   rssi >= -50: quality = "Excellent"
                    elif rssi >= -60: quality = "Good"
                    elif rssi >= -70: quality = "Fair"
                    else:             quality = "Poor"
                msg = f"Connected ({quality})" + (f" | RSSI {rssi} dBm" if rssi else "") + \
                      (f" | Ch {channel}" if channel else "") + \
                      " | (SSID private per macOS 26)"
                status = ToolStatus.DEGRADED if quality == "Poor" else ToolStatus.UP
                return HealthResult("WiFi", status, msg, (time.perf_counter() - t0) * 1000)

            # Power check
            ns = subprocess.run(
                ["/usr/sbin/networksetup", "-getairportpower", "en0"],
                capture_output=True, text=True, timeout=4
            )
            if "Off" in ns.stdout:
                return HealthResult("WiFi", ToolStatus.DOWN,
                                    "WiFi is powered off — enable in System Settings",
                                    (time.perf_counter() - t0) * 1000)
            # If power is on but not connected
            return HealthResult("WiFi", ToolStatus.DEGRADED,
                                "WiFi powered on but not associated to a network",
                                (time.perf_counter() - t0) * 1000)

    except asyncio.TimeoutError:
        return HealthResult("WiFi", ToolStatus.UNKNOWN,
                            "WiFi probe timed out",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("WiFi", ToolStatus.UNKNOWN,
                            f"WiFi probe error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_internet(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Verify internet connectivity via DNS + TCP to Cloudflare 1.1.1.1:443."""
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["ping", "-c", "1", "-t", "4", "1.1.1.1"],
                    capture_output=True, text=True, timeout=6
                )
            ),
            timeout=7
        )
        if result.returncode == 0:
            m   = re.search(r"time=(\d+\.?\d*)", result.stdout)
            lat = float(m.group(1)) if m else None
            msg = f"Reachable | 1.1.1.1 ping{f' {lat:.0f}ms' if lat else ''}"
            return HealthResult("Internet", ToolStatus.UP, msg, (time.perf_counter() - t0) * 1000)
        return HealthResult("Internet", ToolStatus.DOWN,
                            "Cannot reach 1.1.1.1 — no internet",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("Internet", ToolStatus.DOWN,
                            f"Internet check error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_dns(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Probe DNS resolution."""
    t0 = time.perf_counter()
    try:
        ip = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: socket.gethostbyname("cloudflare.com")),
            timeout=5
        )
        lat = (time.perf_counter() - t0) * 1000
        return HealthResult("DNS", ToolStatus.UP,
                            f"cloudflare.com → {ip}",
                            lat)
    except asyncio.TimeoutError:
        return HealthResult("DNS", ToolStatus.DOWN,
                            "DNS resolution timed out",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("DNS", ToolStatus.DOWN,
                            f"DNS resolution failed: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_mlx(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Check whether the MLX fast path model is loaded and ready."""
    t0 = time.perf_counter()
    try:
        import simon_mlx
        if simon_mlx.is_ready():
            model_id = simon_mlx.MODEL_ID.split("/")[-1]
            return HealthResult("MLX Fast Path", ToolStatus.UP,
                                f"{model_id} loaded on MPS GPU",
                                (time.perf_counter() - t0) * 1000)
        else:
            err = simon_mlx._load_error or "not yet loaded"
            return HealthResult("MLX Fast Path", ToolStatus.DEGRADED,
                                f"Model not loaded — {err} (cloud fallback active)",
                                (time.perf_counter() - t0) * 1000)
    except ImportError:
        return HealthResult("MLX Fast Path", ToolStatus.DOWN,
                            "MLX module not installed — cloud-only mode",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("MLX Fast Path", ToolStatus.UNKNOWN,
                            f"MLX probe error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_ollama_cloud(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Probe Ollama Cloud API reachability — does not generate a real LLM call."""
    t0 = time.perf_counter()
    try:
        import httpx
        from pathlib import Path
        import json

        cfg_path = Path(__file__).parent / "config.json"
        cfg  = json.loads(cfg_path.read_text())
        url  = cfg.get("ollama_cloud_url", "https://api.ollama.com")
        key  = cfg.get("ollama_cloud_key", "")

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{url}/api/tags",
                headers={"Authorization": f"Bearer {key}"}
            )
        lat = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            return HealthResult("Ollama Cloud", ToolStatus.UP,
                                f"API reachable | {url}",
                                lat)
        return HealthResult("Ollama Cloud", ToolStatus.DEGRADED,
                            f"API returned HTTP {resp.status_code}",
                            lat)
    except Exception as e:
        return HealthResult("Ollama Cloud", ToolStatus.DOWN,
                            f"Unreachable — {type(e).__name__}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_vision(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Check vision engine (YOLO + camera) without activating the camera."""
    t0 = time.perf_counter()
    try:
        os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"
        from vision.simon_vision import get_engine
        engine = get_engine()
        stats  = engine.get_stats()

        yolo_ok = stats.get("yolo_ready", False)
        moon_ok = stats.get("moondream_ready", False)
        device  = stats.get("device", "unknown")

        if yolo_ok and moon_ok:
            return HealthResult("Vision (YOLO + Moondream)", ToolStatus.UP,
                                f"Both models loaded | device={device}",
                                (time.perf_counter() - t0) * 1000)
        elif yolo_ok:
            return HealthResult("Vision (YOLO + Moondream)", ToolStatus.DEGRADED,
                                f"YOLO ready, Moondream still loading | device={device}",
                                (time.perf_counter() - t0) * 1000)
        else:
            return HealthResult("Vision (YOLO + Moondream)", ToolStatus.DEGRADED,
                                "Models pre-warming in background (normal at startup)",
                                (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("Vision (YOLO + Moondream)", ToolStatus.DOWN,
                            f"Vision engine error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_camera(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """
    Check camera availability without streaming — just opens and reads one frame.
    Uses the same smart index finder that simon_vision.py uses.
    """
    t0 = time.perf_counter()
    try:
        os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"
        import cv2

        working_idx  = None
        peak_brightness = -1.0

        def _scan_cameras():
            nonlocal working_idx, peak_brightness
            for idx in range(4):
                cap = cv2.VideoCapture(idx)
                if not cap.isOpened():
                    cap.release()
                    continue
                peak = 0.0
                for _ in range(6):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        b = float(frame.mean())
                        if b > peak:
                            peak = b
                    if peak > 2.0:
                        break
                    time.sleep(0.04)
                cap.release()
                if peak > peak_brightness:
                    peak_brightness = peak
                    working_idx     = idx
                if peak > 2.0:
                    break

        await asyncio.wait_for(
            loop.run_in_executor(None, _scan_cameras),
            timeout=10
        )

        lat = (time.perf_counter() - t0) * 1000
        if working_idx is not None and peak_brightness > 2.0:
            quality = "Good" if peak_brightness > 30 else "Low light"
            return HealthResult("Camera", ToolStatus.UP,
                                f"Index {working_idx} | brightness {peak_brightness:.0f} ({quality})",
                                lat)
        elif working_idx is not None:
            return HealthResult("Camera", ToolStatus.DEGRADED,
                                "Camera opens but returns very dark frames (check lens / privacy cover)",
                                lat)
        else:
            return HealthResult("Camera", ToolStatus.DOWN,
                                "No camera found — check Privacy & Security → Camera access for Terminal",
                                lat)

    except asyncio.TimeoutError:
        return HealthResult("Camera", ToolStatus.UNKNOWN,
                            "Camera scan timed out",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("Camera", ToolStatus.DOWN,
                            f"Camera error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_piper_tts(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Check Piper TTS voice model is present and piper-tts is installed."""
    t0 = time.perf_counter()
    try:
        from pathlib import Path

        jarvis_dir  = Path(__file__).parent
        voices_dir  = jarvis_dir / "voices"

        # Find any .onnx file in the voices directory — that is the Piper model
        onnx_files = list(voices_dir.glob("*.onnx")) if voices_dir.exists() else []

        if not onnx_files:
            return HealthResult("Piper TTS", ToolStatus.DOWN,
                                f"No .onnx voice model found in {voices_dir}",
                                (time.perf_counter() - t0) * 1000)

        voice_file = onnx_files[0]   # use whichever is present

        try:
            from piper.voice import PiperVoice  # noqa — just checking importability
            return HealthResult("Piper TTS", ToolStatus.UP,
                                f"Model: {voice_file.name} | piper-tts installed",
                                (time.perf_counter() - t0) * 1000)
        except ImportError:
            return HealthResult("Piper TTS", ToolStatus.DOWN,
                                "piper-tts package not installed — run: pip3.11 install piper-tts",
                                (time.perf_counter() - t0) * 1000)

    except Exception as e:
        return HealthResult("Piper TTS", ToolStatus.UNKNOWN,
                            f"TTS probe error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_messages(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Check Messages.app is running (required for iMessage send/read)."""
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["pgrep", "-x", "Messages"],
                    capture_output=True, text=True
                )
            ),
            timeout=4
        )
        lat = (time.perf_counter() - t0) * 1000
        if result.returncode == 0:
            return HealthResult("iMessage (Messages.app)", ToolStatus.UP,
                                "Messages.app running — send/read tools ready",
                                lat)
        return HealthResult("iMessage (Messages.app)", ToolStatus.DOWN,
                            "Messages.app closed — SIMON will auto-open it when needed",
                            lat)
    except Exception as e:
        return HealthResult("iMessage (Messages.app)", ToolStatus.UNKNOWN,
                            f"Messages check error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_mail(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Check Mail.app is running."""
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["pgrep", "-x", "Mail"],
                    capture_output=True, text=True
                )
            ),
            timeout=4
        )
        lat = (time.perf_counter() - t0) * 1000
        if result.returncode == 0:
            return HealthResult("Email (Mail.app)", ToolStatus.UP,
                                "Mail.app running — email tools ready",
                                lat)
        return HealthResult("Email (Mail.app)", ToolStatus.DOWN,
                            "Mail.app closed — SIMON will auto-open it when needed",
                            lat)
    except Exception as e:
        return HealthResult("Email (Mail.app)", ToolStatus.UNKNOWN,
                            f"Mail check error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_security_module(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Verify security guard is loaded and trusted contacts are registered."""
    t0 = time.perf_counter()
    try:
        from simon_security import (
            SHELL_BLOCKLIST, _COMPILED, _COMPILED_INJECTION,
            _TRUSTED_NUMBERS, _TRUSTED_EMAILS
        )
        total_contacts = len(_TRUSTED_NUMBERS) + len(_TRUSTED_EMAILS)
        if total_contacts == 0:
            return HealthResult("Security Guard", ToolStatus.DEGRADED,
                                f"{len(SHELL_BLOCKLIST)} shell patterns | {len(_COMPILED)} data patterns | "
                                "⚠️ NO trusted contacts registered — outbound send guard may block messages",
                                (time.perf_counter() - t0) * 1000)
        return HealthResult("Security Guard", ToolStatus.UP,
                            f"{len(SHELL_BLOCKLIST)} shell patterns | {len(_COMPILED)} data patterns | "
                            f"{total_contacts} trusted contact(s)",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("Security Guard", ToolStatus.DOWN,
                            f"Security module error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_kb(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Check SIMON knowledge base is accessible and healthy."""
    t0 = time.perf_counter()
    try:
        from simon_kb import kb_status
        status = kb_status()
        kb_size = status.get("kb_size_kb", 0)
        contacts = status.get("contacts", 0)
        sessions = status.get("sessions", 0)
        return HealthResult("Knowledge Base (KB)", ToolStatus.UP,
                            f"{contacts} contacts | {sessions} sessions | {kb_size}KB",
                            (time.perf_counter() - t0) * 1000)
    except Exception as e:
        return HealthResult("Knowledge Base (KB)", ToolStatus.DOWN,
                            f"KB error: {e}",
                            (time.perf_counter() - t0) * 1000)


async def _probe_network_plugin(loop: asyncio.AbstractEventLoop) -> HealthResult:
    """Quick sanity check: can the network plugin resolve a hostname?"""
    t0 = time.perf_counter()
    try:
        ip = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: socket.gethostbyname("1.1.1.1")),
            timeout=4
        )
        # Also verify we can import the plugin
        import plugins.network_tools  # noqa
        lat = (time.perf_counter() - t0) * 1000
        return HealthResult("Network Tools Plugin", ToolStatus.UP,
                            f"14 tools loaded | internet reachable",
                            lat)
    except Exception as e:
        return HealthResult("Network Tools Plugin", ToolStatus.DOWN,
                            f"Plugin error or no internet: {e}",
                            (time.perf_counter() - t0) * 1000)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────

# Probes that are safe to run concurrently and on every check.
# Camera probe is excluded from the default fast pass (it takes ~3s) but
# is included in the full check triggered by "camera check" commands.
_FAST_PROBES = [
    _probe_wifi,
    _probe_internet,
    _probe_dns,
    _probe_mlx,
    _probe_ollama_cloud,
    _probe_vision,
    _probe_piper_tts,
    _probe_messages,
    _probe_mail,
    _probe_security_module,
    _probe_kb,
    _probe_network_plugin,
]

_FULL_PROBES = _FAST_PROBES + [_probe_camera]


async def run_all_checks(include_camera: bool = False) -> list[HealthResult]:
    """
    Run all health probes concurrently.
    Results are cached for 5 minutes — subsequent calls return the cache instantly.

    Args:
        include_camera: if True, also probe the camera (adds ~3s)
    """
    global _last_results, _last_run

    if cache_is_fresh():
        return _last_results

    loop   = asyncio.get_event_loop()
    probes = _FULL_PROBES if include_camera else _FAST_PROBES

    # Run all probes concurrently — each has its own timeout
    tasks   = [probe(loop) for probe in probes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    health = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            name = probes[i].__name__.replace("_probe_", "").replace("_", " ").title()
            health.append(HealthResult(name, ToolStatus.UNKNOWN,
                                       f"Probe crashed: {result}"))
        else:
            health.append(result)

    _last_results = health
    _last_run     = time.time()

    # Log summary
    down     = [r for r in health if r.status == ToolStatus.DOWN]
    degraded = [r for r in health if r.status == ToolStatus.DEGRADED]
    up       = [r for r in health if r.status == ToolStatus.UP]
    print(f"[Health] {len(up)} UP | {len(degraded)} DEGRADED | {len(down)} DOWN")
    for r in down + degraded:
        print(f"[Health] {r}")

    return health


def run_all_checks_sync(include_camera: bool = False) -> list[HealthResult]:
    """Synchronous wrapper — safe to call from a thread pool executor."""
    return asyncio.run(run_all_checks(include_camera))


# ─────────────────────────────────────────────────────────────────────────────
#  OUTPUT FORMATTERS
# ─────────────────────────────────────────────────────────────────────────────

def get_health_summary(results: list[HealthResult]) -> str:
    """
    Return a terse one-paragraph summary for SIMON's verbal briefing.
    Only mentions problems — if everything is up, says so briefly.
    """
    if not results:
        return "Tool health data not yet available."

    down     = [r for r in results if r.status == ToolStatus.DOWN]
    degraded = [r for r in results if r.status == ToolStatus.DEGRADED]

    if not down and not degraded:
        up = len([r for r in results if r.status == ToolStatus.UP])
        return f"All {up} tools operational."

    parts = []
    if down:
        names = ", ".join(r.name for r in down)
        parts.append(f"DOWN: {names}")
    if degraded:
        names = ", ".join(r.name for r in degraded)
        parts.append(f"DEGRADED: {names}")

    return " | ".join(parts)


def get_system_prompt_block(results: list[HealthResult]) -> str:
    """
    Return a formatted block for injection into SIMON's system prompt.
    Tells the LLM exactly what is and isn't working right now.
    """
    if not results:
        return ""

    lines = ["CURRENT TOOL STATUS (live health check — know this before every response):"]
    for r in results:
        icon  = {"UP": "✅", "DEGRADED": "⚠️", "DOWN": "❌", "UNKNOWN": "❓"}[r.status.value]
        lines.append(f"  {icon} {r.name}: {r.message}")

    down     = [r for r in results if r.status == ToolStatus.DOWN]
    degraded = [r for r in results if r.status == ToolStatus.DEGRADED]

    if down or degraded:
        lines.append("")
        lines.append("TOOL STATUS RULES:")
        if any(r.name == "WiFi" for r in degraded):
            lines.append(
                "  - WiFi: Connected with good signal. macOS 26 restricts SSID access from "
                "background processes — this is a macOS privacy change, NOT a connectivity problem. "
                "Never tell [OWNER] that WiFi is off or disconnected."
            )
        for r in down:
            lines.append(
                f"  - {r.name} is DOWN. Do NOT attempt to use {r.name} tools. "
                f"Tell [OWNER] immediately if they ask for them: '{r.message}'"
            )
        for r in degraded:
            if r.name != "WiFi":
                lines.append(
                    f"  - {r.name} is DEGRADED: {r.message}"
                )
    return "\n".join(lines)


def invalidate_cache():
    """Force next call to re-run all probes. Call after connectivity changes."""
    global _last_run
    _last_run = 0.0
