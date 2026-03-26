"""
S.I.M.O.N. Plugin — Android Bridge (v1.1)
==========================================
Simon-X Solutions | [OWNER_NAME]

Full Android device integration via ADB over WiFi.
No USB cable needed after first-time pairing.

SETUP (one-time, on your Android phone):
  1. Settings → About Phone → tap "Build Number" 7 times to enable Developer Options
  2. Settings → Developer Options → enable "USB Debugging" and "Wireless Debugging"
  3. In "Wireless Debugging" → tap "Pair device with pairing code"
  4. Note the IP:port and pairing code shown
  5. On your Mac, run: adb pair <IP>:<PORT>  (use the pairing port, NOT 5555)
  6. Enter the pairing code when prompted
  7. Then run: adb connect <PHONE_IP>:5555
  8. Update config.json → "android" → "adb_host" with your phone's IP
  9. Say "Simon, connect to my phone" to verify

After pairing once, SIMON reconnects automatically whenever you're on the same WiFi.

Tools:
  android_connect          — Connect/reconnect to phone over WiFi ADB
  android_status           — Battery, WiFi, device info, ADB connection state
  android_read_sms         — Read SMS inbox (last N messages)
  android_send_sms         — Send an SMS to any number
  android_notifications    — Read active notifications on phone
  android_call_log         — Recent incoming/outgoing/missed calls
  android_contacts_search  — Search contacts by name or number
  android_make_call        — Dial a phone number
  android_end_call         — End the current call
  android_open_app         — Open an app by name
  android_screenshot       — Take a screenshot of the phone screen
  android_get_location     — Get last known GPS location (if enabled)
  android_list_apps        — List installed apps
  android_device_info      — Hardware specs, Android version, carrier info

Voice commands:
  "Simon, read my text messages"
  "Simon, read my last 10 texts"
  "Simon, send a text to Mom saying I'm on my way"
  "Simon, what notifications do I have on my phone?"
  "Simon, show me my recent calls"
  "Simon, search contacts for John"
  "Simon, call 555-867-5309"
  "Simon, end the call"
  "Simon, take a screenshot of my phone"
  "Simon, open YouTube on my phone"
  "Simon, what's my phone battery?"
  "Simon, connect to my phone"
  "Simon, show me my phone info"
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────

METADATA = {
    "name":        "Android Bridge",
    "description": "Full Android device control via ADB WiFi — SMS, calls, notifications, apps, screenshots",
    "version":     "1.2",
    "author":      "Simon-X Solutions",
}

# Load config
_cfg_path = Path(__file__).parent.parent / "config.json"
try:
    _cfg = json.loads(_cfg_path.read_text())
except Exception:
    _cfg = {}

_android_cfg      = _cfg.get("android", {})
ADB_HOST          = _android_cfg.get("adb_host", "")            # home WiFi IP
ADB_HOST_TAILSCALE= _android_cfg.get("adb_host_tailscale", "")  # Tailscale IP — works anywhere
ADB_PORT          = _android_cfg.get("adb_port", 5555)
DEVICE_NAME       = _android_cfg.get("device_name", "Android Phone")
ADB_TIMEOUT       = 10  # seconds for most commands
ADB_SERIAL        = f"{ADB_HOST}:{ADB_PORT}" if ADB_HOST else ""
ADB_SERIAL_TS     = f"{ADB_HOST_TAILSCALE}:{ADB_PORT}" if ADB_HOST_TAILSCALE else ""

# Active serial — resolved at runtime, prefers WiFi, falls back to Tailscale
_active_serial: str = ADB_SERIAL

# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "android_connect",
            "description": "Connect or reconnect to [OWNER]'s Android phone over WiFi via ADB. Use when asked 'connect to my phone', 'reconnect to Android', 'pair phone', or when other android tools fail with connection error.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_status",
            "description": "Get phone status: battery level, charging state, WiFi network, signal strength, ADB connection state. Use when asked 'phone battery', 'phone status', 'is my phone connected', 'check my phone'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_read_sms",
            "description": "Read SMS text messages from the phone inbox. Use when asked 'read my texts', 'read my messages', 'any new texts', 'what did X text me', 'show SMS'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":  {"type": "integer", "description": "Number of messages to read (default 10, max 50)"},
                    "filter": {"type": "string",  "description": "Optional: filter by contact name or phone number"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_send_sms",
            "description": "Send an SMS text message from the phone. Use when asked 'send a text to', 'text X saying', 'SMS X', 'message X'. ALWAYS confirm before sending.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number":  {"type": "string", "description": "Phone number to send to (digits only, e.g. XXXXXXXXXX)"},
                    "message": {"type": "string", "description": "Text message content to send"}
                },
                "required": ["number", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_notifications",
            "description": "Read active notifications currently showing on the phone screen. Use when asked 'what notifications do I have', 'check my phone notifications', 'any alerts on my phone', 'what's on my phone screen'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max notifications to return (default 15)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_call_log",
            "description": "Read recent call history (incoming, outgoing, missed calls) from the phone. Use when asked 'recent calls', 'missed calls', 'who called me', 'call history', 'did anyone call'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":     {"type": "integer", "description": "Number of call records to return (default 15)"},
                    "call_type": {"type": "string",  "description": "Filter: 'missed', 'incoming', 'outgoing', or 'all' (default: all)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_contacts_search",
            "description": "Search phone contacts by name or number. Use when asked 'find contact', 'look up X in my contacts', 'what's X's number', 'search contacts for'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name or partial number to search for"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_make_call",
            "description": "Dial a phone number on the Android phone. Use when asked 'call X', 'dial X', 'phone X'. Always confirm the number before dialing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "string", "description": "Phone number to dial (digits only)"}
                },
                "required": ["number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_end_call",
            "description": "End or reject the current phone call. Use when asked 'end call', 'hang up', 'reject call'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_open_app",
            "description": "Open an app on the Android phone by name. Use when asked 'open X on my phone', 'launch X', 'start X app'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name to open (e.g. 'YouTube', 'Spotify', 'Maps', 'Gmail')"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_screenshot",
            "description": "Take a screenshot of the current phone screen. Use when asked 'screenshot my phone', 'what's on my phone screen', 'capture phone screen'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_get_location",
            "description": "Get the phone's last known GPS location. Use when asked 'where is my phone', 'phone location', 'phone GPS coordinates'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_list_apps",
            "description": "List installed apps on the phone. Use when asked 'what apps are on my phone', 'list installed apps', 'is X installed'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional search string to filter app list"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "android_device_info",
            "description": "Get detailed device info: Android version, model, manufacturer, carrier, IMEI, storage. Use when asked 'phone info', 'what Android version', 'phone specs', 'phone model'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# ADB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _adb(*args, timeout: int = ADB_TIMEOUT, input_data: Optional[bytes] = None) -> subprocess.CompletedProcess:
    """Run an adb command targeting the active device (WiFi or Tailscale)."""
    cmd = ["adb"]
    if _active_serial:
        cmd += ["-s", _active_serial]
    cmd += list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        input=input_data,
    )


def _adb_shell(shell_cmd: str, timeout: int = ADB_TIMEOUT) -> str:
    """Run adb shell <cmd> and return stdout as a decoded string."""
    result = _adb("shell", shell_cmd, timeout=timeout)
    return result.stdout.decode("utf-8", errors="replace").strip()


def _is_connected() -> bool:
    """Check if the active serial is currently reachable via ADB."""
    if not _active_serial:
        return False
    try:
        result = subprocess.run(
            ["adb", "-s", _active_serial, "get-state"],
            capture_output=True, timeout=3
        )
        return result.returncode == 0 and b"device" in result.stdout
    except Exception:
        return False


def _try_connect_serial(serial: str) -> bool:
    """Attempt to connect to a specific ADB serial. Returns True on success."""
    if not serial:
        return False
    try:
        # Check if already connected
        r = subprocess.run(["adb", "-s", serial, "get-state"], capture_output=True, timeout=3)
        if r.returncode == 0 and b"device" in r.stdout:
            return True
        # Try connecting
        r = subprocess.run(["adb", "connect", serial], capture_output=True, timeout=6)
        out = r.stdout.decode().strip().lower()
        if "connected" in out and "unable" not in out and "failed" not in out:
            # Verify it's actually up
            r2 = subprocess.run(["adb", "-s", serial, "get-state"], capture_output=True, timeout=3)
            return r2.returncode == 0 and b"device" in r2.stdout
    except Exception:
        pass
    return False


def _ensure_connected() -> Optional[str]:
    """Return None if connected (updating _active_serial), or an error string if not."""
    global _active_serial
    if not ADB_HOST and not ADB_HOST_TAILSCALE:
        return (
            "Android phone not configured. "
            "Add 'android': {'adb_host': '<phone_ip>', 'adb_port': 5555} to config.json, "
            "then enable Wireless Debugging on your phone and say 'Simon, connect to my phone'."
        )
    # Try current active serial first (fast path)
    if _active_serial and _try_connect_serial(_active_serial):
        return None
    # Try WiFi first
    if _try_connect_serial(ADB_SERIAL):
        _active_serial = ADB_SERIAL
        print(f"[Android] Connected via WiFi ({ADB_SERIAL})")
        return None
    # Fall back to Tailscale
    if _try_connect_serial(ADB_SERIAL_TS):
        _active_serial = ADB_SERIAL_TS
        print(f"[Android] Connected via Tailscale ({ADB_SERIAL_TS}) — phone not on local WiFi")
        return None
    return (
        f"Cannot reach {DEVICE_NAME} via WiFi ({ADB_SERIAL}) or Tailscale ({ADB_SERIAL_TS}). "
        "Check that Wireless Debugging is ON and Tailscale is running on the phone. "
        "Say 'Simon, connect to my phone' to retry."
    )
    return None


def _fmt_ts(ms_str: str) -> str:
    """Convert millisecond epoch string to human-readable date/time."""
    try:
        ts = int(ms_str) / 1000
        return datetime.fromtimestamp(ts).strftime("%b %d %I:%M %p")
    except Exception:
        return ms_str


def _clean_text(text: str) -> str:
    """Remove ADB content query artifacts from text fields."""
    return re.sub(r"^\w+=", "", text).strip()


# Common app package name mapping
_APP_PACKAGES = {
    "youtube":   "com.google.android.youtube",
    "spotify":   "com.spotify.music",
    "gmail":     "com.google.android.gm",
    "maps":      "com.google.android.apps.maps",
    "chrome":    "com.android.chrome",
    "instagram": "com.instagram.android",
    "twitter":   "com.twitter.android",
    "x":         "com.twitter.android",
    "tiktok":    "com.zhiliaoapp.musically",
    "netflix":   "com.netflix.mediaclient",
    "camera":    "com.android.camera2",
    "settings":  "com.android.settings",
    "phone":     "com.android.dialer",
    "messages":  "com.google.android.apps.messaging",
    "discord":   "com.discord",
    "whatsapp":  "com.whatsapp",
    "facebook":  "com.facebook.katana",
    "snapchat":  "com.snapchat.android",
    "amazon":    "com.amazon.mShop.android.shopping",
    "calculator":"com.android.calculator2",
    "calendar":  "com.google.android.calendar",
    "clock":     "com.android.deskclock",
    "contacts":  "com.android.contacts",
    "files":     "com.google.android.documentsui",
    "photos":    "com.google.android.apps.photos",
    "play":      "com.android.vending",
    "drive":     "com.google.android.apps.docs",
    "sheets":    "com.google.android.apps.spreadsheets",
    "docs":      "com.google.android.apps.docs.editors.docs",
    "meet":      "com.google.android.apps.tachyon",
    "duo":       "com.google.android.apps.tachyon",
    "zoom":      "us.zoom.videomeetings",
    "teams":     "com.microsoft.teams",
    "outlook":   "com.microsoft.office.outlook",
    "vpn":       "com.nordvpn.android",
}


# ─────────────────────────────────────────────────────────────────────────────
# Tool executors
# ─────────────────────────────────────────────────────────────────────────────

async def execute(name: str, args: dict) -> Optional[str]:
    loop = asyncio.get_event_loop()

    # ── android_connect ──────────────────────────────────────────────────────
    if name == "android_connect":
        if not ADB_HOST and not ADB_HOST_TAILSCALE:
            return (
                "No Android device configured yet. "
                "Add 'android': {'adb_host': '<phone_ip>', 'adb_host_tailscale': '<tailscale_ip>', "
                "'adb_port': 5555, 'device_name': 'My Phone'} to config.json."
            )
        # Try WiFi first, then Tailscale
        for serial, label in [(ADB_SERIAL, "WiFi"), (ADB_SERIAL_TS, "Tailscale")]:
            if not serial:
                continue
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda s=serial: subprocess.run(["adb", "connect", s], capture_output=True, timeout=8)
                )
                out = result.stdout.decode().strip().lower()
                if "connected" in out and "unable" not in out and "failed" not in out:
                    global _active_serial
                    _active_serial = serial
                    model = await loop.run_in_executor(
                        None, lambda: _adb_shell("getprop ro.product.model", timeout=5)
                    )
                    return f"✅ Connected to {DEVICE_NAME} ({model}) via {label} at {serial}."
            except Exception:
                continue
        return (
            f"Could not connect to {DEVICE_NAME} via WiFi ({ADB_SERIAL}) or Tailscale ({ADB_SERIAL_TS}). "
            "Make sure Wireless Debugging is ON and Tailscale is running on the phone."
        )

    # ── android_status ───────────────────────────────────────────────────────
    elif name == "android_status":
        err = _ensure_connected()
        if err:
            return err
        try:
            battery_raw = await loop.run_in_executor(
                None, lambda: _adb_shell("dumpsys battery", timeout=6)
            )
            wifi_raw = await loop.run_in_executor(
                None, lambda: _adb_shell("dumpsys wifi | grep 'mWifiInfo\\|SSID\\|RSSI\\|ipAddress'", timeout=6)
            )
            level     = re.search(r"level:\s*(\d+)",     battery_raw)
            status    = re.search(r"status:\s*(\d+)",    battery_raw)
            temp      = re.search(r"temperature:\s*(\d+)", battery_raw)
            charging  = {1: "unknown", 2: "charging", 3: "discharging", 4: "not charging", 5: "full"}
            charge_str = charging.get(int(status.group(1)), "?") if status else "?"
            temp_c    = round(int(temp.group(1)) / 10, 1) if temp else "?"
            batt_str  = f"{level.group(1)}%" if level else "?"
            ssid_m    = re.search(r'SSID: ([^,]+)',   wifi_raw)
            rssi_m    = re.search(r'RSSI: (-?\d+)',   wifi_raw)
            ssid_str  = ssid_m.group(1).strip() if ssid_m else "unknown"
            rssi_str  = f"{rssi_m.group(1)} dBm" if rssi_m else "?"
            conn_path = "Tailscale" if _active_serial == ADB_SERIAL_TS else "WiFi"
            return (
                f"{DEVICE_NAME} status — "
                f"Battery: {batt_str} ({charge_str}, {temp_c}°C) | "
                f"WiFi: {ssid_str} ({rssi_str}) | "
                f"ADB: connected via {conn_path} ({_active_serial})"
            )
        except Exception as e:
            return f"Status error: {e}"

    # ── android_read_sms ─────────────────────────────────────────────────────
    elif name == "android_read_sms":
        err = _ensure_connected()
        if err:
            return err
        limit  = min(int(args.get("limit", 10)), 50)
        filter_q = args.get("filter", "").strip().lower()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: _adb_shell(
                    f'content query --uri content://sms/inbox '
                    f'--projection address:person:body:date:read '
                    f'--sort "date DESC"',  # --limit removed: not supported on Android 13+
                    timeout=10
                )
            )
            if not raw or "No result found" in raw:
                return "No SMS messages found in inbox."

            # Parse rows
            rows = raw.strip().split("Row:")
            messages = []
            for row in rows[1:]:
                addr_m  = re.search(r'address=([^,]+)',  row)
                body_m  = re.search(r'body=(.+?)(?:,\s*\w+=|$)', row, re.DOTALL)
                date_m  = re.search(r'date=(\d+)',       row)
                read_m  = re.search(r'read=(\d)',         row)
                addr    = addr_m.group(1).strip()  if addr_m  else "?"
                body    = body_m.group(1).strip()  if body_m  else "(no content)"
                date    = _fmt_ts(date_m.group(1)) if date_m  else "?"
                read    = "✓" if read_m and read_m.group(1) == "1" else "●"
                # Apply filter
                if filter_q and filter_q not in addr.lower() and filter_q not in body.lower():
                    continue
                messages.append(f"[{read}] {date} | From: {addr} — {body[:120]}")
                if len(messages) >= limit:
                    break

            if not messages:
                return f"No messages found matching '{filter_q}'." if filter_q else "No messages in inbox."
            header = f"Last {len(messages)} SMS messages" + (f" (filtered: {filter_q})" if filter_q else "")
            return header + ":\n" + "\n".join(messages)
        except Exception as e:
            return f"SMS read error: {e}"

    # ── android_send_sms ─────────────────────────────────────────────────────
    elif name == "android_send_sms":
        err = _ensure_connected()
        if err:
            return err
        number  = re.sub(r"[^\d+]", "", args.get("number", ""))
        message = args.get("message", "").strip()
        if not number or not message:
            return "Both a phone number and message text are required."
        try:
            import time as _time
            # Use SMS send via service call (Android 5+)
            escaped = message.replace("'", "\\'").replace('"', '\\"')
            cmd = (
                f"service call isms 5 i32 0 s16 'com.android.mms.service' "
                f"s16 'null' s16 '{number}' s16 'null' s16 '{escaped}' "
                f"s16 'null' s16 'null' i32 0 i64 0"
            )
            result = await loop.run_in_executor(
                None,
                lambda: _adb_shell(cmd, timeout=10)
            )
            sent_ok = "result" in result.lower() or not result

            if sent_ok:
                # Android 14+ (API 34+) blocks ADB writes to the SMS content provider.
                # Only the default SMS app can write to content://sms. Attempting the
                # insert on Android 14+ silently returns null and writes nothing.
                # Check API level and skip the insert on affected devices.
                ts_ms = int(_time.time() * 1000)
                api_str = await loop.run_in_executor(
                    None, lambda: _adb_shell("getprop ro.build.version.sdk", timeout=5)
                )
                try:
                    api_level = int(api_str.strip())
                except ValueError:
                    api_level = 0

                if api_level >= 34:
                    # Log to local file as a paper trail since SMS DB write is blocked
                    import pathlib as _pl
                    log_path = _pl.Path(__file__).parent.parent / "sms_sent_log.txt"
                    try:
                        with open(log_path, "a") as _f:
                            _f.write(
                                f"[{_time.strftime('%Y-%m-%d %H:%M:%S')}] "
                                f"TO={number} MSG={message}\n"
                            )
                    except Exception:
                        pass
                    return (
                        f"✅ SMS sent to {number}: \"{message}\"\n"
                        f"(Android {api_level} restricts sent-box writes — "
                        f"message dispatched but won't appear in Google Messages history)"
                    )

                # Android 13 and below — attempt content provider insert
                body_escaped = message.replace("'", "'\\''")
                insert_cmd = (
                    f"content insert --uri content://sms/sent "
                    f"--bind address:s:{number} "
                    f"--bind body:s:'{body_escaped}' "
                    f"--bind date:l:{ts_ms} "
                    f"--bind date_sent:l:{ts_ms} "
                    f"--bind read:i:1 "
                    f"--bind seen:i:1 "
                    f"--bind status:i:-1 "
                    f"--bind type:i:2"
                )
                await loop.run_in_executor(None, lambda: _adb_shell(insert_cmd, timeout=8))
                return f"✅ SMS sent to {number}: \"{message}\""

            # Fallback: open SMS compose intent
            fallback = (
                f"am start -a android.intent.action.SENDTO "
                f"-d sms:{number} --es sms_body '{escaped}' --ez exit_on_sent true"
            )
            await loop.run_in_executor(None, lambda: _adb_shell(fallback, timeout=8))
            return f"✅ SMS compose opened on phone to {number}. Tap Send to confirm: \"{message}\""
        except Exception as e:
            return f"SMS send error: {e}"

    # ── android_notifications ────────────────────────────────────────────────
    elif name == "android_notifications":
        err = _ensure_connected()
        if err:
            return err
        limit = min(int(args.get("limit", 15)), 30)
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: _adb_shell("dumpsys notification --noredact", timeout=10)
            )
            # Extract notification entries
            entries = []
            blocks = re.split(r'NotificationRecord\(', raw)
            for block in blocks[1:]:
                pkg_m  = re.search(r'pkg=(\S+)',             block)
                title_m = re.search(r'android\.title=([^\n,]+)', block)
                text_m  = re.search(r'android\.text=([^\n,]+)',  block)
                time_m  = re.search(r'when=(\d+)',            block)
                pkg    = pkg_m.group(1)  if pkg_m   else "?"
                title  = title_m.group(1).strip() if title_m else ""
                text   = text_m.group(1).strip()  if text_m  else ""
                ts     = _fmt_ts(time_m.group(1)) if time_m  else ""
                # Skip system packages that aren't user-facing
                skip = {"android", "com.android.systemui", "com.android.phone", "com.android.server"}
                if pkg in skip:
                    continue
                short_pkg = pkg.split(".")[-1].replace("android", "").strip(".")
                entry = f"[{ts}] {short_pkg.upper()}"
                if title:
                    entry += f" — {title}"
                if text:
                    entry += f": {text[:100]}"
                entries.append(entry)
                if len(entries) >= limit:
                    break

            if not entries:
                return "No active notifications on your phone right now."
            return f"{len(entries)} notification(s) on {DEVICE_NAME}:\n" + "\n".join(entries)
        except Exception as e:
            return f"Notifications error: {e}"

    # ── android_call_log ─────────────────────────────────────────────────────
    elif name == "android_call_log":
        err = _ensure_connected()
        if err:
            return err
        limit     = min(int(args.get("limit", 15)), 50)
        call_type = args.get("call_type", "all").lower()
        # call types: 1=incoming, 2=outgoing, 3=missed, 4=voicemail
        type_map  = {"incoming": "1", "outgoing": "2", "missed": "3", "all": None}
        type_filter = type_map.get(call_type)
        try:
            where_clause = f" --where 'type={type_filter}'" if type_filter else ""
            raw = await loop.run_in_executor(
                None,
                lambda: _adb_shell(
                    f'content query --uri content://call_log/calls '
                    f'--projection number:name:type:duration:date '
                    f'--sort "date DESC"'  # --limit not supported on Android 13+
                    f'{where_clause}',
                    timeout=10
                )
            )
            if not raw or "No result found" in raw:
                return "No call log entries found."

            type_labels = {"1": "📞 Incoming", "2": "📤 Outgoing", "3": "❌ Missed", "4": "📬 Voicemail"}
            rows = raw.strip().split("Row:")
            lines = []
            for row in rows[1:]:
                num_m  = re.search(r'number=([^,]+)',   row)
                name_m = re.search(r'name=([^,]+)',     row)
                type_m = re.search(r'type=(\d)',        row)
                dur_m  = re.search(r'duration=(\d+)',   row)
                date_m = re.search(r'date=(\d+)',       row)
                number = num_m.group(1).strip()  if num_m  else "?"
                cname  = name_m.group(1).strip() if name_m and name_m.group(1) != "null" else number
                ctype  = type_labels.get(type_m.group(1) if type_m else "?", "?")
                dur    = f"{int(dur_m.group(1))//60}m {int(dur_m.group(1))%60}s" if dur_m else "?"
                ts     = _fmt_ts(date_m.group(1)) if date_m else "?"
                lines.append(f"{ctype} | {ts} | {cname} | {dur}")

            if not lines:
                return f"No {call_type} calls found."
            header = f"Recent {call_type} calls ({len(lines)} entries):"
            return header + "\n" + "\n".join(lines)
        except Exception as e:
            return f"Call log error: {e}"

    # ── android_contacts_search ──────────────────────────────────────────────
    elif name == "android_contacts_search":
        err = _ensure_connected()
        if err:
            return err
        query = args.get("query", "").strip()
        if not query:
            return "Please provide a name or number to search for."
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: _adb_shell(
                    'content query --uri content://contacts/phones '
                    '--projection display_name:number '
                    f'--where "display_name LIKE \'%{query}%\' OR number LIKE \'%{query}%\'"',
                    timeout=10
                )
            )
            if not raw or "No result found" in raw:
                return f"No contacts found matching '{query}'."
            rows = raw.strip().split("Row:")
            results = []
            for row in rows[1:]:
                name_m = re.search(r'display_name=([^,]+)', row)
                num_m  = re.search(r'number=([^,\n]+)',     row)
                name   = name_m.group(1).strip() if name_m else "?"
                number = num_m.group(1).strip()  if num_m  else "?"
                results.append(f"{name}: {number}")
            return f"Contacts matching '{query}':\n" + "\n".join(results[:20])
        except Exception as e:
            return f"Contacts search error: {e}"

    # ── android_make_call ────────────────────────────────────────────────────
    elif name == "android_make_call":
        err = _ensure_connected()
        if err:
            return err
        number = re.sub(r"[^\d+]", "", args.get("number", ""))
        if not number:
            return "Please provide a phone number to call."
        try:
            await loop.run_in_executor(
                None,
                lambda: _adb_shell(
                    f"am start -a android.intent.action.CALL -d tel:{number}",
                    timeout=8
                )
            )
            return f"📞 Dialing {number} on {DEVICE_NAME}..."
        except Exception as e:
            return f"Call error: {e}"

    # ── android_end_call ─────────────────────────────────────────────────────
    elif name == "android_end_call":
        err = _ensure_connected()
        if err:
            return err
        try:
            await loop.run_in_executor(
                None,
                lambda: _adb_shell("input keyevent 6", timeout=5)  # KEYCODE_ENDCALL
            )
            return "Call ended."
        except Exception as e:
            return f"End call error: {e}"

    # ── android_open_app ─────────────────────────────────────────────────────
    elif name == "android_open_app":
        err = _ensure_connected()
        if err:
            return err
        app_name = args.get("app_name", "").strip().lower()
        if not app_name:
            return "Please specify which app to open."
        # Look up known package name
        package = _APP_PACKAGES.get(app_name)
        if not package:
            # Try finding it from installed packages
            installed_raw = await loop.run_in_executor(
                None,
                lambda: _adb_shell(f"pm list packages -3 | grep -i '{app_name}'", timeout=8)
            )
            matches = [line.replace("package:", "").strip()
                       for line in installed_raw.splitlines() if line.strip()]
            if not matches:
                return f"App '{app_name}' not found on {DEVICE_NAME}. Say 'Simon, list apps' to see installed apps."
            package = matches[0]
        try:
            await loop.run_in_executor(
                None,
                lambda: _adb_shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", timeout=8)
            )
            return f"✅ Opened {app_name.title()} on {DEVICE_NAME}."
        except Exception as e:
            return f"Open app error: {e}"

    # ── android_screenshot ───────────────────────────────────────────────────
    elif name == "android_screenshot":
        err = _ensure_connected()
        if err:
            return err
        try:
            ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = Path(__file__).parent.parent / f"android_screenshot_{ts}.png"
            raw_bytes = await loop.run_in_executor(
                None,
                lambda: _adb("exec-out", "screencap", "-p", timeout=15).stdout
            )
            if not raw_bytes:
                return "Screenshot capture returned empty data. Is the screen on?"
            save_path.write_bytes(raw_bytes)
            return f"✅ Screenshot saved: {save_path.name} ({len(raw_bytes)//1024} KB)"
        except Exception as e:
            return f"Screenshot error: {e}"

    # ── android_get_location ─────────────────────────────────────────────────
    elif name == "android_get_location":
        err = _ensure_connected()
        if err:
            return err
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: _adb_shell(
                    "dumpsys location | grep -E 'lastLocation|Lat|Long|accuracy' | head -20",
                    timeout=10
                )
            )
            if not raw:
                return "No location data available. Make sure Location Services are enabled on the phone."
            # Extract coordinates
            lat_m = re.search(r'lat(?:itude)?[=:]\s*(-?[\d.]+)', raw, re.IGNORECASE)
            lon_m = re.search(r'lon(?:gitude)?[=:]\s*(-?[\d.]+)', raw, re.IGNORECASE)
            acc_m = re.search(r'acc(?:uracy)?[=:]\s*([\d.]+)',     raw, re.IGNORECASE)
            if lat_m and lon_m:
                lat  = lat_m.group(1)
                lon  = lon_m.group(1)
                acc  = f" ±{acc_m.group(1)}m" if acc_m else ""
                maps_url = f"https://maps.google.com/?q={lat},{lon}"
                return f"📍 {DEVICE_NAME} location: {lat}, {lon}{acc} | Maps: {maps_url}"
            return f"Could not parse coordinates. Raw data: {raw[:300]}"
        except Exception as e:
            return f"Location error: {e}"

    # ── android_list_apps ────────────────────────────────────────────────────
    elif name == "android_list_apps":
        err = _ensure_connected()
        if err:
            return err
        filter_str = args.get("filter", "").strip().lower()
        try:
            # -3 = third-party apps only, -e = enabled only
            cmd = "pm list packages -3 -e"
            if filter_str:
                cmd += f" | grep -i '{filter_str}'"
            raw = await loop.run_in_executor(
                None, lambda: _adb_shell(cmd, timeout=10)
            )
            packages = [line.replace("package:", "").strip()
                        for line in raw.splitlines() if line.strip()]
            if not packages:
                msg = f"No apps found matching '{filter_str}'." if filter_str else "No user-installed apps found."
                return msg
            # Make package names human-readable
            names = [p.split(".")[-1].replace("-", " ").title() for p in packages]
            if filter_str:
                return f"Apps matching '{filter_str}': {', '.join(names)}"
            return f"{len(packages)} user-installed apps on {DEVICE_NAME}: {', '.join(sorted(names)[:40])}" + \
                   (" (showing first 40)" if len(packages) > 40 else "")
        except Exception as e:
            return f"List apps error: {e}"

    # ── android_device_info ──────────────────────────────────────────────────
    elif name == "android_device_info":
        err = _ensure_connected()
        if err:
            return err
        try:
            props = [
                "ro.product.brand",
                "ro.product.model",
                "ro.product.manufacturer",
                "ro.build.version.release",
                "ro.build.version.sdk",
                "ro.build.display.id",
                "ro.telephony.default_network",
                "gsm.operator.alpha",
                "gsm.version.baseband",
            ]
            results = {}
            for prop in props:
                val = await loop.run_in_executor(
                    None, lambda p=prop: _adb_shell(f"getprop {p}", timeout=4)
                )
                results[prop] = val.strip() or "N/A"

            # Storage
            storage_raw = await loop.run_in_executor(
                None, lambda: _adb_shell("df /data | tail -1", timeout=5)
            )
            storage_parts = storage_raw.split()
            if len(storage_parts) >= 4:
                used_mb  = round(int(storage_parts[2]) / 1024)
                avail_mb = round(int(storage_parts[3]) / 1024)
                storage_str = f"{used_mb}MB used, {avail_mb}MB free"
            else:
                storage_str = "N/A"

            return (
                f"{DEVICE_NAME} info — "
                f"{results['ro.product.manufacturer']} {results['ro.product.model']} | "
                f"Android {results['ro.build.version.release']} (SDK {results['ro.build.version.sdk']}) | "
                f"Build: {results['ro.build.display.id']} | "
                f"Carrier: {results['gsm.operator.alpha']} | "
                f"Storage: {storage_str}"
            )
        except Exception as e:
            return f"Device info error: {e}"

    return None
