# S.I.M.O.N. HQ Repair Runbook
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.4 | March 21, 2026**
**Maintained for: simon-hq LLM repair assistant (llama3.1:8b)**

---

## PURPOSE

This document is the authoritative source of truth for diagnosing and repairing S.I.M.O.N.
It is formatted for both human engineers and the simon-hq LLM repair assistant.
When SIMON calls `ask_hq_for_repair_guidance(error)`, this runbook is the knowledge base.

Every fix here has been validated in production. Every issue was encountered on real hardware.
No speculative fixes — only things that actually worked.

---

## SYSTEM IDENTITY

```
SIMON (S.I.M.O.N.) = AI assistant for [OWNER_NAME] / Simon-X Solutions
Owner: [OWNER_NAME] | your@email.com | +1XXXXXXXXXX
Company: Simon-X Solutions ([CITY] MSP)

Mac (primary):       macOS, M5 MacBook Air, 24GB RAM
                     Tailscale: YOUR_MAC_TAILSCALE_IP | Local: 10.0.0.xxx
                     SIMON FastAPI server runs here on :8765

simon-hq (brain):   Ubuntu, i7 CPU, 33.4GB RAM, 982GB disk
                     Tailscale: YOUR_HQ_TAILSCALE_IP | Local: YOUR_HQ_LOCAL_IP
                     HQ API on :8200 | Ollama on :11434 | ChromaDB on :8100

Pixel 9a (personal phone):  Android 16 (SDK 36)
                              Tailscale: YOUR_ANDROID_TAILSCALE_IP | Home WiFi: YOUR_ANDROID_LOCAL_IP
                              ADB port: 5555

Critical: Pixel 9a = [OWNER]'s PERSONAL phone. Mac Messages = [OWNER]'s WORK line.
Never confuse these. android_send_sms for personal. iMessage for work.
```

---

## QUICK DIAGNOSTIC COMMAND

Run this first. It covers 80% of issues:

```bash
# On Mac — runs the self-healing engine
cd ~/Projects/AI-Projects/jarvis
python3.11 simon_healer.py --diagnose-only
```

For full auto-repair:
```bash
python3.11 simon_healer.py
```

The healer checks: Mail.app, Messages.app, port 8765 conflicts, KB integrity,
Piper TTS, log file size, ADB connectivity. It fixes what it can automatically.

---

## SIMON STARTUP COMMAND

```bash
cd ~/Projects/AI-Projects/jarvis && ./start_simon.sh
```

If `start_simon.sh` doesn't exist or fails:
```bash
cd ~/Projects/AI-Projects/jarvis
python3.11 jarvis.py
```

Port bind confirmation in log: `Application startup complete` on port 8765.

---

## KNOWN ISSUES — INDEX

| # | Issue | Symptom | Section |
|---|-------|---------|---------|
| 1 | SIMON won't start — syntax error | Exits immediately | §A |
| 2 | SIMON won't start — port conflict | Address already in use | §B |
| 3 | SIMON won't start — module missing | ModuleNotFoundError | §C |
| 4 | HQ Bridge not connecting | Conversational responses slow/fail | §D |
| 5 | All requests go to Cloud only | HQ never responds | §E |
| 6 | Tool executor routing broken | hq_ask() called for every request | §F |
| 7 | Messages not sending | AppleScript errors | §G |
| 8 | Email not working | Mail.app errors | §H |
| 9 | ADB not connecting (Android) | android_* tools fail | §I |
| 10 | ADB content queries broken | Returns usage text, no data | §J |
| 11 | Tailscale ADB — authentication failed | Connection refused or auth error | §K |
| 12 | LM Studio not responding | lm_* tools time out | §L |
| 13 | Knowledge base errors | KB read/write failures | §M |
| 14 | Piper TTS not found | Falls back to say -v Reed | §N |
| 15 | Vision/camera failures | Camera tools return nothing | §O |
| 16 | MLX not loading | Third-tier fallback unavailable | §P |
| 17 | Log file oversized | Slow log reads | §Q |
| 18 | Health check shows everything DOWN | False positive cascade | §R |
| 19 | FastAPI deprecation warning | startup event deprecated | §S |
| 20 | HQ circular import at startup | _register_callback_endpoint error | §T |
| 21 | Ollama Cloud API unreachable | All tool calls fail | §U |

---

## §A — SIMON WON'T START: SYNTAX ERROR

**Symptom:** Process exits with `SyntaxError: invalid syntax` and a file/line reference.

**Diagnosis:**
```bash
python3.11 -c "import ast; ast.parse(open('jarvis.py').read())" && echo OK
```

**Fix:**
1. Note the exact line number from the error output.
2. Open `jarvis.py` at that line.
3. Common causes: unclosed string, missing colon after `def`/`if`/`for`, stray character.
4. After fixing: re-run the syntax check command above before starting.

**Prevention:** Always run syntax check after edits.

---

## §B — SIMON WON'T START: PORT CONFLICT

**Symptom:** `OSError: [Errno 48] Address already in use: ('0.0.0.0', 8765)`

**Diagnosis:**
```bash
lsof -ti tcp:8765
```

**Fix:**
```bash
lsof -ti tcp:8765 | xargs kill -9
# Then restart SIMON
```

**Automated fix:** `simon_healer.py` class `FixPortConflict` handles this automatically.

**Root cause:** A previous SIMON process didn't terminate cleanly (power loss, force quit, etc.)

---

## §C — SIMON WON'T START: MISSING MODULE

**Symptom:** `ModuleNotFoundError: No module named 'xyz'`

**Fix:**
```bash
pip3.11 install xyz --break-system-packages
```

**Common missing packages and their installs:**
```bash
pip3.11 install httpx fastapi uvicorn websockets --break-system-packages
pip3.11 install chromadb sentence-transformers --break-system-packages
pip3.11 install ultralytics opencv-python --break-system-packages
pip3.11 install mlx mlx-lm --break-system-packages  # Apple Silicon only
```

**Note:** Always use `pip3.11` not `pip3` — SIMON runs on Python 3.11.

---

## §D — HQ BRIDGE NOT CONNECTING

**Symptom:** Log shows `[HQ] ⚠️ HQ not reachable at startup` repeatedly.
Conversational requests are slow (falling through to Cloud instead of HQ).

**Step 1 — Check network:**
```bash
# From Mac
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health
# Expected: {"status":"ok", ...}

# Via Tailscale
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health
ping -c3 YOUR_HQ_TAILSCALE_IP
```

**Step 2 — If network is fine but HQ API down:**
```bash
ssh user@YOUR_HQ_TAILSCALE_IP   # or ssh simon-hq (if ~/.ssh/config has alias)
sudo systemctl status simon-hq-api
sudo systemctl restart simon-hq-api
journalctl -u simon-hq-api -n 30 --no-pager
```

**Step 3 — If Ollama is down:**
```bash
sudo systemctl status ollama
sudo systemctl restart ollama
# Verify
curl http://localhost:11434/api/tags
```

**Step 4 — If Tailscale is the issue:**
```bash
# On Mac
tailscale status
tailscale ping YOUR_HQ_TAILSCALE_IP

# On HQ
sudo tailscale up
tailscale status
```

**Expected HQ behavior in v4.4:** Bridge starts non-blocking. SIMON runs fine even if HQ is offline.
Conversational requests fall back to Mistral Large Cloud automatically. Not a critical failure.

---

## §E — HQ NOT RESPONDING TO CONVERSATIONAL REQUESTS

**Symptom:** Simple questions like "how are you" always route to Cloud, never HQ.

**Diagnosis — check routing logic:**
```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep "\[Routing\]\|\[HQ\]"
```

**Expected log for conversational request:**
```
[Routing] "good morning" → conversational → HQ
[HQ] ✅ Response received in 1.2s
```

**If log shows cloud routing for conversational:**
Check the `_is_conversational()` function in `jarvis.py`. It uses keyword matching.
If none of the conversational keywords match, the request falls through to Cloud.

**Add keywords if needed** — look for `_is_conversational()` in `jarvis.py` and extend the list.

---

## §F — TOOL EXECUTOR ROUTING BROKEN (HISTORIC BUG — v4.2)

**Symptom:** Every user request becomes a call to `hq_ask()` with the raw message text.
Log shows: `[TOOL] hq_ask({"prompt": "turn off my lights"})`

**This was the major v4.2 bug, fixed in v4.3.**

**Root cause:** HQ was receiving the tool definitions in its payload. HQ then "used" hq_ask
as a catch-all tool instead of answering conversationally.

**Fix:** Ensure `_hq_chat_simple()` in `jarvis.py` does NOT pass any `tools` parameter.
The HQ endpoint must receive: `{"model": ..., "messages": [...]}` — no tools array.

**Verify fix:**
```bash
grep -A 20 "_hq_chat_simple" jarvis.py | grep -i tool
# Should return nothing — no tools= in the HQ payload
```

---

## §G — MESSAGES NOT SENDING / IMESSAGE TOOLS FAILING

**Symptom:** `send_message` returns AppleScript error, or messages appear not sent.

**Step 1 — Check Messages.app is running:**
```bash
pgrep -x Messages && echo "Running" || (open -a Messages && echo "Opened")
```

**Step 2 — Check Full Disk Access:**
`System Settings → Privacy & Security → Full Disk Access → Terminal = ON`
Without this, SIMON cannot read the Messages SQLite database.

**Step 3 — Verify iMessage account signed in:**
Open Messages.app → Messages menu → Settings → iMessage tab.
Apple ID must be signed in and iMessage enabled.

**Step 4 — Phone number format:**
SIMON normalizes to 10-digit format. `+1(555)867-5309` → `5558675309`.
If a contact has an unusual format, test with `search_contacts` tool first.

**Important device routing rule:**
- Mac Messages / iMessage = [OWNER]'s WORK phone line
- Android Pixel 9a = [OWNER]'s personal phone
- "My Love" contact = ALWAYS android_send_sms, NEVER iMessage
- Contact +1XXXXXXXXXX = [OWNER]'s personal notification number

**Auto-heal:** `simon_healer.py` class `FixMessagesApp` auto-reopens Messages.app if closed.

---

## §H — EMAIL NOT WORKING

**Symptom:** `send_email` or `read_emails` returns AppleScript error.

**Step 1 — Check Mail.app is running:**
```bash
pgrep -x Mail && echo "Running" || (open -a Mail && echo "Opened")
```

**Step 2 — Mail.app initialization delay:**
Mail.app takes 3-5 seconds to initialize accounts after opening.
SIMON waits 3 seconds after auto-opening, but first command after open may fail.
Simply repeat the request — second attempt always works.

**Step 3 — Check Full Disk Access (same as Messages):**
`System Settings → Privacy & Security → Full Disk Access → Terminal = ON`

**Auto-heal:** `simon_healer.py` class `FixMailApp` auto-reopens Mail.app if closed.

---

## §I — ADB NOT CONNECTING TO ANDROID (PIXEL 9A)

**Full Tailscale connection setup (anywhere access):**
```bash
adb connect YOUR_ANDROID_TAILSCALE_IP:5555
# Phone will show: "Allow USB debugging?" — tap ALWAYS ALLOW
```

**Home WiFi connection (faster, local only):**
```bash
adb connect YOUR_ANDROID_LOCAL_IP:5555
# Phone will show: "Allow USB debugging?" — tap ALWAYS ALLOW
```

**Verify connection:**
```bash
adb devices
# Should show: YOUR_ANDROID_TAILSCALE_IP:5555    device
#              YOUR_ANDROID_LOCAL_IP:5555       device
```

**If connection drops or fails:**
```bash
# Restart ADB server
adb kill-server && adb start-server
adb connect YOUR_ANDROID_TAILSCALE_IP:5555
# Tap Allow on phone when prompted
```

**CRITICAL:** After `adb kill-server`, phone will show auth dialog AGAIN.
Must tap Allow on phone before any ADB commands work.
Each IP address (WiFi and Tailscale) requires its own separate authorization.

**If phone shows "offline" or "unauthorized":**
1. On phone: Developer Options → Revoke USB debugging authorizations
2. Re-connect and tap Allow
3. Re-authorize BOTH the WiFi IP and the Tailscale IP separately

**Android Developer Options must have:**
- Developer Options: ON
- USB debugging: ON
- Wireless debugging: ON
- Wireless ADB pairing: configured (one-time pairing with adb pair ip:port)

**Auto-heal:** `simon_healer.py` class `FixADBReconnect` auto-reconnects via WiFi then Tailscale.

---

## §J — ADB CONTENT QUERIES RETURNING USAGE TEXT (ANDROID 13+)

**Symptom:** `content query --uri content://sms/inbox --limit 10` returns usage/help text
instead of actual SMS data. android_read_sms returns "No messages found" or usage output.

**Root cause:** Android 13+ (SDK 33+) removed the `--limit` flag from `content query`.
The Pixel 9a runs Android 16 (SDK 36). This flag is completely gone.

**Fix:** Remove `--limit N` from ALL `content query` commands.
Do Python-side slicing after fetching instead:
```bash
# BROKEN (Android 13+):
adb shell content query --uri content://sms/inbox --limit 10

# CORRECT:
adb shell content query --uri content://sms/inbox
# Then slice results in Python: lines[-10:]
```

**This is fixed in `android_bridge.py` v1.2.** If the issue recurs, check no `--limit`
flags were added back to content query commands in the plugin.

---

## §K — TAILSCALE ADB "FAILED TO AUTHENTICATE"

**Symptom:** `adb connect YOUR_ANDROID_TAILSCALE_IP:5555` shows "failed to authenticate to YOUR_ANDROID_TAILSCALE_IP:5555"

**Root cause:** The Tailscale IP requires separate authorization from the WiFi IP.
Each IP address is treated as a different connection by Android.

**Fix:**
```bash
adb connect YOUR_ANDROID_TAILSCALE_IP:5555
# Watch phone screen — it will show "Allow USB debugging?" dialog
# Tap "Always allow from this computer"
# Wait 3-5 seconds, then:
adb -s YOUR_ANDROID_TAILSCALE_IP:5555 get-state
# Should return: device
```

**If the phone screen is locked, no dialog appears:**
Unlock the phone first, then run `adb connect` again.

**If Wireless Debugging is OFF:**
Phone → Developer Options → Wireless debugging = ON.
This must be ON for TCP/IP ADB to work. WiFi must be connected.

---

## §L — LM STUDIO NOT RESPONDING

**Symptom:** `lm_status` returns "LM Studio server is not running on simon-hq"
or `lm_ask` times out with ConnectError on port 1234.

**LM Studio is a manual-start app.** It does NOT auto-start with the system.

**Fix — Start LM Studio server on simon-hq:**
1. Open LM Studio on simon-hq (it's a GUI app)
2. Load a model using the model browser (left panel)
3. Click the `<-> API` tab OR `Developer` tab in the left sidebar
4. Click `Start Server`
5. Server will start on port 1234

**Verify from Mac:**
```bash
curl -s http://YOUR_HQ_TAILSCALE_IP:1234/v1/models | python3 -m json.tool
# Or say: "Simon, LM Studio status"
```

**Config key:** `config.json` → `"lm_studio_url": "http://YOUR_HQ_TAILSCALE_IP:1234"`
If the URL changes, update this key and restart SIMON.

**Plugin location:** `plugins/lm_studio.py` — 4 tools: lm_status, lm_ask, lm_list_models, lm_compare

---

## §M — KNOWLEDGE BASE ERRORS

**Symptom:** `kb_search` or `kb_store` returns SQLite error. Log shows `[KB] ❌ Error`.

**Step 1 — Check integrity:**
```bash
cd ~/Projects/AI-Projects/jarvis
python3.11 -c "
import sqlite3
conn = sqlite3.connect('simon_kb.db')
result = conn.execute('PRAGMA integrity_check').fetchone()
print(result[0])
conn.close()
"
# Expected: ok
```

**Step 2 — Auto-repair (if integrity check fails):**
```bash
python3.11 simon_healer.py --diagnose-only
# If it shows "KB Integrity Failure", run:
python3.11 simon_healer.py
# The FixKBIntegrity class runs VACUUM + integrity_check
```

**Step 3 — Manual VACUUM:**
```bash
python3.11 -c "
import sqlite3
conn = sqlite3.connect('simon_kb.db')
conn.execute('VACUUM')
conn.commit()
conn.close()
print('VACUUM complete')
"
```

**Step 4 — If DB is unrecoverable:**
The KB is a cache — losing it means SIMON forgets stored facts but otherwise works normally.
```bash
mv simon_kb.db simon_kb.db.broken
# SIMON will auto-create a new empty KB on next startup
```

**Auto-heal:** `simon_healer.py` class `FixKBIntegrity` handles this automatically.

---

## §N — PIPER TTS NOT FOUND

**Symptom:** Voice output uses macOS `say -v Reed` instead of Piper.
SIMON still speaks, but with slightly different voice quality.

**Locate Piper:**
```bash
which piper
ls ~/.local/bin/piper
ls /usr/local/bin/piper
ls /opt/homebrew/bin/piper
```

**If missing — reinstall:**
1. Go to: https://github.com/rhasspy/piper/releases
2. Download the macOS ARM64 binary (`piper_macos_aarch64.tar.gz`)
3. Extract and place binary at `~/.local/bin/piper`
4. Make executable: `chmod +x ~/.local/bin/piper`
5. Verify: `piper --help`

**Voice model location:** `~/Projects/AI-Projects/jarvis/voices/alan.onnx`
Both the binary AND the .onnx model file must exist for Piper to work.

**Auto-heal:** `simon_healer.py` class `FixPiperTTS` detects missing Piper and reports instructions.
(Auto-reinstall is not attempted — binary download requires user action.)

---

## §O — VISION / CAMERA FAILURES

**Symptom:** `vision_describe` or `vision_ask` returns nothing or camera error.

**Step 1 — Camera permissions:**
`System Settings → Privacy & Security → Camera → Terminal = ON`
Then restart SIMON. Camera permission changes require restart.

**Step 2 — YOLO model file:**
```bash
ls -lh ~/Projects/AI-Projects/jarvis/yolo26n.pt
# Should exist and be ~5-6MB
```

If missing:
```bash
cd ~/Projects/AI-Projects/jarvis
python3.11 -c "from ultralytics import YOLO; YOLO('yolo26n.pt')"
```

**Step 3 — Moondream (vision analysis) on HQ:**
```bash
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health | python3 -m json.tool
# Look for llama3.2-vision or moondream in the response
```

If llama3.2-vision is not available on HQ:
```bash
ssh user@YOUR_HQ_TAILSCALE_IP
ollama pull llama3.2-vision:11b
```

**Step 4 — Check camera index:**
If wrong camera is used (external vs built-in):
SIMON defaults to camera index 0. MacBooks built-in is usually index 0.
If you have a USB camera that conflicts, disconnect it and restart SIMON.

---

## §P — MLX NOT LOADING (EMERGENCY FALLBACK)

**Symptom:** Log shows `[MLX] ❌ Not available`. Third-tier fallback cannot activate.

**Note:** MLX is NOT pre-loaded by design — it only loads when needed (HQ + Cloud both fail).
"Not available" at idle is completely normal. Only investigate if SIMON falls to Tier 3 and fails.

**Verify MLX is installed:**
```bash
python3.11 -c "import mlx_lm; print('MLX OK')"
```

**If missing:**
```bash
pip3.11 install mlx mlx-lm --break-system-packages
```

**MLX model must be downloaded:**
```bash
python3.11 -c "
from mlx_lm import load
model, tokenizer = load('mlx-community/Mistral-7B-Instruct-v0.2-4bit')
print('Model ready')
"
```
This downloads ~4GB on first run. Will cache to HuggingFace cache dir.

**MLX only works on Apple Silicon (M1/M2/M3/M4/M5).** Intel Macs cannot use it.

---

## §Q — LOG FILE OVERSIZED

**Symptom:** `jarvis.log` exceeds 50MB. Log reads are slow. `tail -f` is sluggish.

**Quick check:**
```bash
ls -lh ~/Projects/AI-Projects/jarvis/jarvis.log
```

**Manual trim (keep last 10,000 lines):**
```bash
cd ~/Projects/AI-Projects/jarvis
tail -10000 jarvis.log > jarvis.log.tmp && mv jarvis.log.tmp jarvis.log
```

**Auto-heal:** `simon_healer.py` class `FixStaleLog` trims to 10,000 lines and archives the rest.

**Prevention:** Consider adding logrotate config or a nightly trim cron.

---

## §R — HEALTH CHECK SHOWS EVERYTHING DOWN (FALSE POSITIVES)

**These are EXPECTED and not real problems:**
- **MLX Fast Path** — intentionally not pre-loaded at startup. Always shows DEGRADED.
- **Vision (YOLO + Moondream)** — Moondream not pre-loaded. Always shows DEGRADED.
- **Mail.app / Messages.app** — SIMON auto-opens them. Shows DOWN briefly after startup.

**Real problems worth investigating:**
- **WiFi DOWN** — actual connectivity issue on Mac
- **Internet DOWN** — no outbound internet access
- **DNS DOWN** — DNS resolution failing
- **Piper TTS DOWN** — voice model missing (see §N)
- **Knowledge Base DOWN** — SQLite issue (see §M)
- **HQ DOWN** — simon-hq is unreachable (see §D)

**Health check trigger for self-heal:** After 5 minutes of sustained DOWN, SIMON:
1. Sends a macOS Notification Center alert
2. Calls `simon_healer.py` automatically (if `HEALER_AVAILABLE = True`)

---

## §S — FASTAPI DEPRECATION WARNING (HISTORIC — FIXED IN v4.3)

**Symptom:** `DeprecationWarning: on_event is deprecated, use lifespan instead`

**Root cause:** Using `@app.on_event("startup")` which was deprecated in FastAPI 0.93+.

**Fix (already applied in v4.4):**
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_bg_kb_sync())
    asyncio.create_task(_bg_mlx_prewarm())
    # ... all background tasks ...
    yield

app = FastAPI(title="S.I.M.O.N. v4.4", lifespan=lifespan)
```

If this warning recurs, check that `app = FastAPI(...)` includes `lifespan=lifespan`
and there are NO remaining `@app.on_event("startup")` decorators in `jarvis.py`.

---

## §T — HQ BRIDGE CIRCULAR IMPORT AT STARTUP (HISTORIC — FIXED IN v4.3)

**Symptom:** `AttributeError: module 'jarvis' has no attribute 'app'`
at startup when `hq_bridge.py` loads.

**Root cause:** `_on_load()` in `hq_bridge.py` called `_register_callback_endpoint()`
directly at module import time, before `jarvis.app` existed.

**Fix (already applied in v4.4 in `plugins/hq_bridge.py`):**
```python
async def _deferred_init():
    await asyncio.sleep(0.5)   # Wait for app to fully initialize
    _register_callback_endpoint()
    asyncio.create_task(_startup_handshake())

def _on_load():
    asyncio.create_task(_deferred_init())
```

If this recurs, ensure `_register_callback_endpoint()` is always called from an async
context, never at module-level import time.

---

## §U — OLLAMA CLOUD API UNREACHABLE

**Symptom:** All tool calls fail with timeout or connection error.
Log shows `[Cloud] ❌ Connection error`.

**Step 1 — Check internet:**
```bash
curl -s https://api.ollama.com/api/tags \
  -H "Authorization: Bearer $(python3.11 -c "import json; print(json.load(open('config.json'))['ollama_cloud_key'])")" \
  | head -c 200
```

**Step 2 — Check API key:**
Config: `config.json` → `"ollama_cloud_key"` → your key from https://ollama.com

If key is expired:
1. Go to https://ollama.com (web console)
2. Generate new API key
3. Update `config.json` → `"ollama_cloud_key"`
4. Restart SIMON

**Step 3 — Check model availability:**
```bash
curl -s https://api.ollama.com/api/tags \
  -H "Authorization: Bearer YOUR_KEY" | python3.11 -m json.tool | grep mistral
```
Model in use: `mistral-large-3:675b`

**Fallback behavior if Cloud is down:**
SIMON falls back to HQ (conversational only) or MLX (if loaded).
Tool calls will not execute until Cloud is restored.
This is by design — tool execution requires the large model.

---

## SIMON SELF-REPAIR ENGINE

### Files
- `simon_healer.py` — the engine itself
- `repair.log` — log of all repair actions taken

### CLI Usage
```bash
# Full diagnosis + auto-repair
python3.11 simon_healer.py

# Diagnosis only (no changes)
python3.11 simon_healer.py --diagnose-only

# Ask HQ LLM for guidance on an unknown error
python3.11 simon_healer.py --ask-hq "jarvis.py throws AttributeError on line 1204"
```

### Voice Commands (when SIMON is running)
```
"Simon, run self-diagnostics"
"Simon, repair yourself"
"Simon, what's wrong with you?"
```

### Fix Registry

Each fix is a Python class with `check()` and `fix()` methods:

| Class | Checks | Auto-Fix |
|-------|--------|----------|
| `FixMailApp` | Mail.app running | `open -a Mail` |
| `FixMessagesApp` | Messages.app running | `open -a Messages` |
| `FixPortConflict` | Port 8765 free | `kill -9` conflicting PID |
| `FixKBIntegrity` | SQLite PRAGMA integrity_check | VACUUM + re-check |
| `FixPiperTTS` | Piper binary exists | Reports download URL |
| `FixStaleLog` | jarvis.log < 50MB | Trim to 10k lines, archive rest |
| `FixADBReconnect` | ADB device reachable | `adb connect` via WiFi then Tailscale |

### Adding a New Fix

```python
class FixMyThing(Fix):
    def __init__(self):
        super().__init__("Short Name", "What is broken and why it matters")

    def check(self) -> bool:
        # Return True if the problem IS present
        rc, out, _ = run(["some", "check", "command"])
        return rc != 0

    def fix(self) -> str:
        # Apply the fix. Return human-readable result.
        rc, out, err = run(["the", "fix", "command"])
        return "Fixed: ..." if rc == 0 else f"Failed: {err}"

# Add to FIXES list at bottom of simon_healer.py:
FIXES: list[Fix] = [
    ...,
    FixMyThing(),
]
```

---

## COMPONENT LOCATIONS

```
~/Projects/AI-Projects/jarvis/
├── jarvis.py              ← Main FastAPI server (DO NOT EDIT WHILE RUNNING)
├── simon_healer.py        ← Self-repair engine
├── plugin_loader.py       ← Hot-reload plugin system
├── config.json            ← All settings, API keys, IPs
├── simon_kb.db            ← SQLite knowledge base
├── jarvis.log             ← Main log (tail -f to watch live)
├── repair.log             ← Healer-specific log
├── start_simon.sh         ← Clean startup script
├── restart_simon.sh       ← Kill + restart script
├── plugins/
│   ├── android_bridge.py  ← Pixel 9a ADB integration (v1.2)
│   ├── hq_bridge.py       ← simon-hq communication (v2.1)
│   └── lm_studio.py       ← LM Studio GPU inference (v1.0)
├── docs/                  ← All documentation
├── tools/                 ← Diagnostic utilities
│   ├── diag_camera.py
│   └── diag_hq.py
├── tests/                 ← Test scripts
│   ├── test_hq_vision.py
│   └── test_simon_hq.py
└── _archive/              ← Old backups and one-time scripts
```

---

## CRITICAL CONFIG KEYS

```json
{
  "ollama_cloud_key": "3aa...key...",      ← Cloud LLM API key
  "ollama_cloud_url": "https://api.ollama.com",
  "model":            "mistral-large-3:675b",  ← Cloud model for tools
  "hq_api_url":       "http://YOUR_HQ_TAILSCALE_IP:8200",
  "hq_api_key":       "6c59...key...",     ← HQ API auth
  "hq_model":         "llama3.1:8b",       ← HQ conversational model
  "lm_studio_url":    "http://YOUR_HQ_TAILSCALE_IP:1234",  ← LM Studio API
  "port":             8765,                ← SIMON's own port
  "tts_voice":        "Reed (English (UK))",
  "tts_rate":         168,
  "android": {
    "adb_host":           "YOUR_ANDROID_LOCAL_IP",   ← Home WiFi ADB
    "adb_host_tailscale": "YOUR_ANDROID_TAILSCALE_IP", ← Tailscale ADB (anywhere)
    "adb_port":           5555
  }
}
```

---

## ESCALATION MATRIX

| Severity | Condition | Action |
|----------|-----------|--------|
| 🟢 OK | All systems nominal | Nothing |
| 🟡 DEGRADED | MLX/Vision pre-warm not loaded | Expected — not actionable |
| 🟡 DEGRADED | HQ offline | Monitor; Cloud handles everything |
| 🔴 DOWN | Port conflict | `simon_healer.py` auto-fixes |
| 🔴 DOWN | App closed (Mail/Messages) | `simon_healer.py` auto-fixes |
| 🔴 CRITICAL | jarvis.py SyntaxError | Manual edit required |
| 🔴 CRITICAL | Cloud API unreachable | Check internet, check API key |
| 🔴 CRITICAL | All 3 tiers fail | Emergency restart + diagnostics |

**After 5 minutes sustained DOWN:** SIMON sends macOS notification and attempts self-heal.

---

## VERSION HISTORY — WHAT BROKE AND HOW WE FIXED IT

### v4.2 → v4.3 (Major Bug Resolution)
- **Bug:** Every request routed to `hq_ask()` — SIMON was completely non-responsive to tools
- **Root cause:** Tool definitions included in HQ payload; HQ used hq_ask as catch-all
- **Fix:** Removed tools from HQ payload in `_hq_chat_simple()`
- **Impact:** Restored full tool functionality

### v4.3 → v4.4 (March 2026 — Current)
- Added Android Pixel 9a integration via ADB (plugin: `android_bridge.py`)
- Added Tailscale auto-failover for ADB (WiFi → Tailscale → error)
- Removed `--limit` from Android 16 content queries (SDK 36 broke this)
- Added LM Studio plugin (`lm_studio.py`) for GPU inference on simon-hq
- Added `simon_healer.py` self-repair engine with 7 auto-fixes
- Added `repair_simon` voice tool in jarvis.py
- Added health escalation + macOS Notification Center alerts (5-min sustained DOWN)
- Added Android background monitor (`_bg_android_monitor`) — missed calls + battery alerts
- Added `tool_check_calendar_conflicts()` before calendar creates
- Fixed FastAPI deprecation: `@app.on_event` → `lifespan` context manager
- Fixed HQ Bridge circular import: `_on_load()` → `_deferred_init()` with 0.5s delay
- Documented device routing: Android = personal, Mac iMessage = work
- Archived 37 dead files (backups, one-time scripts) to `_archive/`
- Organized project: `tools/`, `tests/`, `docs/` directories

---

*This runbook is maintained by [OWNER_NAME] / Simon-X Solutions.*
*Last updated: March 21, 2026 — SIMON v4.4*
*Feed this document to the HQ LLM as context for repair guidance requests.*
