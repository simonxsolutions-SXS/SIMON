# S.I.M.O.N. Self-Service Troubleshooting Guide
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.3 | March 21, 2026**

---

## The One Script You Need

Before doing anything else, run this:

```bash
~/Projects/AI-Projects/jarvis/simon_repair.sh
```

This script checks everything automatically — Python, syntax, running processes, apps, network, HQ, config, files, RAM, recent errors — and tells you exactly what's wrong. Run it any time something feels off.

**Options:**

```bash
# Diagnosis only, no changes
./simon_repair.sh --check

# Diagnose and auto-fix everything without prompting
./simon_repair.sh --fix-all

# Show recent log lines after the report
./simon_repair.sh --logs
```

---

## Quick Commands Cheat Sheet

```bash
# ── Start / Stop / Restart ─────────────────────────────────────
~/Projects/AI-Projects/jarvis/start_simon.sh
~/Projects/AI-Projects/jarvis/stop_simon.sh
~/Projects/AI-Projects/jarvis/restart_simon.sh

# ── Check if SIMON is running ──────────────────────────────────
pgrep -f jarvis.py && echo "RUNNING" || echo "STOPPED"

# ── Watch live log (clean) ─────────────────────────────────────
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v "To disable" | grep -v huggingface

# ── Watch only errors and tool calls ──────────────────────────
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -E "TOOL|ERROR|FAIL|SyntaxError|❌|⚠️|✅"

# ── Clear the log ──────────────────────────────────────────────
> ~/Projects/AI-Projects/jarvis/jarvis.log

# ── Check last 20 real log lines ──────────────────────────────
tail -50 ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v huggingface | tail -20

# ── Check what's on port 8765 ──────────────────────────────────
lsof -i tcp:8765

# ── Kill everything and restart clean ─────────────────────────
pkill -9 -f jarvis.py; pkill -9 afplay; lsof -ti tcp:8765 | xargs kill -9 2>/dev/null; sleep 2; ~/Projects/AI-Projects/jarvis/start_simon.sh

# ── Syntax check jarvis.py without running it ─────────────────
/opt/homebrew/bin/python3.11 -m py_compile ~/Projects/AI-Projects/jarvis/jarvis.py && echo "✅ Syntax OK" || echo "❌ Syntax ERROR"
```

---

## Problem 1: SIMON Won't Start

**Symptom:** `start_simon.sh` runs but SIMON never appears in the browser.

### Step 1 — Check the log immediately
```bash
tail -30 ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v huggingface
```

**If you see `SyntaxError`:**
There's a Python syntax error in jarvis.py. The start script will also print it.
- Fix the specific line shown
- Then run `start_simon.sh` again

**If you see `ModuleNotFoundError: No module named 'X'`:**
```bash
/opt/homebrew/bin/python3.11 -m pip install X --break-system-packages
```

**If you see `Address already in use`:**
```bash
lsof -ti tcp:8765 | xargs kill -9
sleep 2
~/Projects/AI-Projects/jarvis/start_simon.sh
```

**If you see `FileNotFoundError: config.json`:**
config.json is missing. Restore it or recreate it — SIMON won't start without it.

### Step 2 — Check if Python is the right version
```bash
/opt/homebrew/bin/python3.11 --version
# Should say: Python 3.11.x
```

### Step 3 — Check the LaunchAgent
```bash
launchctl list | grep simon
# Should show: com.simonx.simon

# If not showing — reload it:
launchctl unload ~/Library/LaunchAgents/com.simonx.simon.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.simonx.simon.plist
sleep 3
launchctl kickstart -k gui/$(id -u)/com.simonx.simon
```

---

## Problem 2: SIMON Talks but Won't Do Anything

**Symptom:** SIMON says the greeting but doesn't respond to "check my messages" or calendar requests.

### Check the log while talking to SIMON
```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -E "\[TOOL\]|\[HQ\]|\[MLX\]"
```

**What you should see when you say something:**
```
[HQ] ✅ Fast response from llama3.1:8b        ← conversational
[TOOL] get_recent_messages({"hours": 24})      ← tool being called
[TOOL] get_recent_messages → [result...]       ← tool returned
```

**If you see `hq_ask` being called for everything** — HQ routing bug (v4.2 regression):
```bash
grep "hq_ask" ~/Projects/AI-Projects/jarvis/jarvis.log | tail -5
```
Fix: Make sure you're running jarvis.py v4.3. The `_hq_chat_simple()` function must NOT send tools to HQ.

**If you see nothing after speaking** — WebSocket may be disconnected:
- Open browser DevTools → Network → WS tab
- You should see an active connection to `ws://localhost:8765/ws/...`
- If not: restart SIMON

**If you see `Connection failed`** — Cloud API issue:
```bash
# Test Cloud API
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer $(python3.11 -c "import json; print(json.load(open('~/Projects/AI-Projects/jarvis/config.json'.replace('~', __import__('os').path.expanduser('~'))))['ollama_cloud_key'])")" \
  https://api.ollama.com/api/tags
# Should return: HTTP 200
```

---

## Problem 3: No Voice / Speech

**Symptom:** SIMON responds in the HUD but nothing plays through the speaker.

### Check if Piper loaded
```bash
grep "TTS" ~/Projects/AI-Projects/jarvis/jarvis.log | tail -5
# Should say: [TTS] Piper loaded: ...voices/en_GB-alan-medium.onnx
```

**If it says `Piper unavailable`** — voice model missing or Piper package broken:
```bash
# Check voice file exists
ls ~/Projects/AI-Projects/jarvis/voices/en_GB-alan-medium.onnx

# Check Piper package
/opt/homebrew/bin/python3.11 -c "from piper.voice import PiperVoice; print('OK')"

# Reinstall if broken
/opt/homebrew/bin/python3.11 -m pip install piper-tts --break-system-packages
```

SIMON will fall back to macOS `say -v Daniel` if Piper is unavailable — you should still hear something. If you hear nothing at all:

```bash
# Test macOS speech directly
say -v Daniel "Testing one two three"

# Test afplay directly
echo "test" | /opt/homebrew/bin/python3.11 -c "
from piper.voice import PiperVoice
import wave, os
v = PiperVoice.load('$(echo ~)/Projects/AI-Projects/jarvis/voices/en_GB-alan-medium.onnx')
with wave.open('/tmp/test_simon.wav', 'wb') as wf:
    v.synthesize_wav('Hello sir, this is SIMON speaking', wf)
"
afplay /tmp/test_simon.wav
```

**If afplay is playing but you can't hear it:**
Check macOS volume and that output device is correct (System Settings → Sound → Output).

---

## Problem 4: Messages Not Working

**Symptom:** SIMON can't read or send iMessages.

### Check Full Disk Access
```bash
# Try reading the Messages database directly
sqlite3 ~/Library/Messages/chat.db "SELECT count(*) FROM message LIMIT 1;" 2>&1
# If it returns a number → access is fine
# If it says "unable to open" → Full Disk Access is OFF
```

**Fix Full Disk Access:**
System Settings → Privacy & Security → Full Disk Access → enable Terminal (or whatever terminal app you use)

### Check Messages.app is open
```bash
pgrep -x Messages && echo "Running" || open -a Messages
```

### Test sending manually
```bash
osascript -e 'tell application "Messages"
  set acct to (first account whose service type = iMessage)
  send "SIMON test" to participant "5558675309" of acct
end tell'
```

---

## Problem 5: Email Not Working

**Symptom:** SIMON can't read or send email.

### Check Mail.app is open and initialized
```bash
pgrep -x Mail && echo "Running" || open -a Mail
# Wait 5 seconds for Mail to load accounts, then try again
```

### Test reading email via AppleScript
```bash
osascript -e 'tell application "Mail"
  return (count of (messages of inbox whose read status is false))
end tell'
# Should return a number (could be 0)
```

### Check Mail accounts are configured
Open Mail.app → Mail menu → Settings → Accounts. All accounts should show green status.

---

## Problem 6: Vision/Camera Not Working

**Symptom:** "What do you see?" returns an error or camera not found.

### Check camera permissions
```bash
# Check if Terminal has camera access (indirect test)
/opt/homebrew/bin/python3.11 -c "
import cv2
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
cap.release()
print('Camera OK' if ret else 'Camera FAILED')
"
```

**If Camera FAILED:**
System Settings → Privacy & Security → Camera → enable Terminal

### Check YOLO model
```bash
ls -lh ~/Projects/AI-Projects/jarvis/yolo26n.pt
# Should be ~5-6MB
```

**If missing:**
```bash
cd ~/Projects/AI-Projects/jarvis
/opt/homebrew/bin/python3.11 -c "from ultralytics import YOLO; YOLO('yolo26n.pt')"
```

### Run camera diagnostic
```bash
/opt/homebrew/bin/python3.11 ~/Projects/AI-Projects/jarvis/diag_camera.py
```

---

## Problem 7: HQ Not Connecting

**Symptom:** Log shows `[HQ] ⚠️ Offline` constantly. Tool calls still work (via Cloud) but no fast conversational responses.

### Step 1 — Ping HQ via Tailscale
```bash
tailscale ping YOUR_HQ_TAILSCALE_IP
# Should return latency like: pong from simon-hq (YOUR_HQ_TAILSCALE_IP) via xxx in 5ms
```

**If ping fails:**
```bash
tailscale status
# If disconnected:
sudo tailscale up
```

### Step 2 — Check HQ API health
```bash
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health | python3.11 -m json.tool
```

Expected:
```json
{
  "status": "ok",
  "ollama": true,
  "chromadb": true
}
```

### Step 3 — SSH to HQ and check services
```bash
ssh user@YOUR_HQ_TAILSCALE_IP

# On HQ:
sudo systemctl status simon-hq-api ollama simon-chroma

# Restart what's down:
sudo systemctl restart simon-hq-api
sudo systemctl restart ollama

# Check HQ API log:
sudo journalctl -u simon-hq-api -f
```

### Step 4 — Check if Ollama has the right model
```bash
# On HQ:
ollama list
# Should show llama3.1:8b

# If missing:
ollama pull llama3.1:8b
```

---

## Problem 8: High RAM / Slowness

**Symptom:** Mac feels sluggish, fans spinning, SIMON slow to respond.

### Check current RAM
```bash
/opt/homebrew/bin/python3.11 -c "
import subprocess, re
vs = subprocess.run(['vm_stat'], capture_output=True, text=True).stdout
mp = subprocess.run(['memory_pressure'], capture_output=True, text=True).stdout
pg = 16384
def get_vs(k):
    m = re.search(rf'{k}:\s+(\d+)', vs)
    return int(m.group(1))*pg if m else 0
def get_mp(k):
    m = re.search(rf'{k}:\s+(\d+)', mp)
    return int(m.group(1))*pg if m else 0
free = get_mp('Pages free') + get_vs('File-backed pages')
used = 24*(1024**3) - free
print(f'Used: {used/(1024**3):.1f}GB / 24GB  |  Free: {free/(1024**3):.1f}GB')
"
```

**If used > 18GB — restart SIMON:**
```bash
~/Projects/AI-Projects/jarvis/restart_simon.sh
```

**If MLX or Moondream loaded unnecessarily:**
```bash
grep "MLX.*ready\|Moondream.*loaded" ~/Projects/AI-Projects/jarvis/jarvis.log | tail -5
```
These should only appear if HQ was offline for 60+ seconds. A restart clears them.

---

## Problem 9: SIMON Starts But HUD Won't Open

**Symptom:** start_simon.sh finishes but Chrome doesn't open, or the page is blank/error.

### Check SIMON is actually serving
```bash
curl -s http://localhost:8765/api/status | python3.11 -m json.tool
# Should return CPU/RAM/disk stats
```

**If connection refused:**
SIMON hasn't fully started yet. Wait 5 more seconds and try again.

**If 500 error:**
hud.html may be missing:
```bash
ls -lh ~/Projects/AI-Projects/jarvis/hud.html
```

**Manually open Chrome:**
```bash
open -a "Google Chrome" http://localhost:8765
```

**Or open in Safari:**
```bash
open http://localhost:8765
```

---

## Problem 10: Contacts/Calendar Wrong or Empty

**Symptom:** SIMON says "No contact found" or "No events today" when there clearly are some.

### Contacts
```bash
# Test AppleScript contacts access directly
osascript -e 'tell application "Contacts"
  return name of first person
end tell'
# If it returns a name, contacts are working
# If permission error: System Settings → Privacy → Contacts → enable Terminal
```

### Calendar
```bash
# Test AppleScript calendar access
osascript -e 'tell application "Calendar"
  return count of calendars
end tell'
# Should return a number > 0
# If permission error: System Settings → Privacy → Calendars → enable Terminal
```

### Reset permissions
If all else fails with permissions: open System Settings → Privacy & Security → find the relevant category → remove Terminal → add Terminal back.

---

## HQ Diagnostics (Advanced)

### Run the HQ diagnostic script
```bash
/opt/homebrew/bin/python3.11 ~/Projects/AI-Projects/jarvis/diag_hq.py
```

### Check all HQ services at once
```bash
ssh user@YOUR_HQ_TAILSCALE_IP "
  echo '=== Services ===' &&
  sudo systemctl is-active simon-hq-api ollama simon-chroma postgresql &&
  echo '=== Ollama Models ===' &&
  ollama list &&
  echo '=== RAM ===' &&
  free -h &&
  echo '=== Disk ===' &&
  df -h /
"
```

### Test HQ vision endpoint
```bash
ssh user@YOUR_HQ_TAILSCALE_IP
python3 ~/Projects/AI-Projects/jarvis/test_hq_vision.py
```

---

## Resetting SIMON Completely (Last Resort)

If everything is broken and you want a clean restart:

```bash
# 1. Kill everything
pkill -9 -f jarvis.py 2>/dev/null
pkill -9 afplay 2>/dev/null
pkill -9 say 2>/dev/null
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null

# 2. Unload LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.simonx.simon.plist 2>/dev/null

# 3. Clear the log
> ~/Projects/AI-Projects/jarvis/jarvis.log

# 4. Verify jarvis.py syntax
/opt/homebrew/bin/python3.11 -m py_compile ~/Projects/AI-Projects/jarvis/jarvis.py && echo "Syntax OK"

# 5. Reload LaunchAgent and start
launchctl load ~/Library/LaunchAgents/com.simonx.simon.plist
sleep 2
launchctl kickstart -k gui/$(id -u)/com.simonx.simon

# 6. Wait and open
sleep 5
open -a "Google Chrome" http://localhost:8765
```

---

## Log Reference — What Each Line Means

| Log Line | Meaning |
|---|---|
| `[Vision] ✅ YOLO ready` | Camera + object detection ready |
| `[HQ] ✅ Online` | simon-hq API is up, llama3.1:8b warm |
| `[HQ] ⚠️ Offline` | simon-hq unreachable, Cloud-only mode |
| `[TTS] Piper loaded` | British Alan voice ready |
| `[Health] 10 UP \| 2 DEGRADED` | 10 tools healthy, 2 degraded (MLX + Moondream = normal) |
| `[TOOL] get_recent_messages(...)` | SIMON executing a tool call |
| `[TOOL] get_recent_messages → ...` | Tool returned a result |
| `[MLX] HQ offline — loading` | HQ has been down 60s, loading local fallback |
| `[KB] 167 contacts \| 20 memory facts` | Knowledge base stats at startup |
| `[Security] Guard active` | Security module loaded and scanning |
| `SyntaxError` | ❌ Python code broken — must fix before SIMON can start |
| `ModuleNotFoundError` | ❌ Python package missing — pip install it |
| `Address already in use` | ❌ Port 8765 already taken — kill old process |

---

## Privacy Permissions Required

SIMON needs all of these. Check them in **System Settings → Privacy & Security**:

| Permission | Required For |
|---|---|
| Full Disk Access | Reading iMessages database |
| Contacts | Searching contacts by name |
| Calendars | Reading and creating events |
| Microphone | Voice input via Web Speech API |
| Camera | Vision tools (YOLO, vision_ask) |
| Automation → System Events | AppleScript execution |
| Automation → Calendar | Calendar AppleScript |
| Automation → Messages | iMessage sending/reading |
| Automation → Mail | Email reading/sending |
| Automation → Contacts | Contact lookup |

If any of these are off, the corresponding tool will fail silently or return a permission error.

---

## Getting Help

If `simon_repair.sh` doesn't find the issue, collect this info:

```bash
# Full diagnostic dump — save this and share it
{
  echo "=== DATE ==="
  date
  echo "=== SIMON RUNNING ==="
  pgrep -f jarvis.py && echo "YES" || echo "NO"
  echo "=== PYTHON VERSION ==="
  /opt/homebrew/bin/python3.11 --version
  echo "=== SYNTAX CHECK ==="
  /opt/homebrew/bin/python3.11 -m py_compile ~/Projects/AI-Projects/jarvis/jarvis.py && echo "OK" || echo "FAIL"
  echo "=== HQ HEALTH ==="
  curl -s --max-time 5 http://YOUR_HQ_TAILSCALE_IP:8200/health || echo "NOT REACHABLE"
  echo "=== LAST 30 LOG LINES ==="
  tail -50 ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v huggingface | tail -30
} 2>&1 | tee /tmp/simon_diag_$(date +%Y%m%d_%H%M%S).txt
echo "Saved to /tmp/simon_diag_*.txt"
```
