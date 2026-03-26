# S.I.M.O.N. Troubleshooting Guide
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.3 | March 21, 2026**

---

## SIMON Won't Start

### Symptom: Process exits immediately
Check the log:
```bash
tail -20 ~/Projects/AI-Projects/jarvis/jarvis.log
```

**If you see `SyntaxError`:**
A Python syntax error in jarvis.py is preventing launch. The `start_simon.sh` script catches this and prints the error. Fix the specific line shown, then re-run the script.

**If you see `ModuleNotFoundError`:**
A Python dependency is missing:
```bash
pip3.11 install [missing_module] --break-system-packages
```

**If you see `Address already in use`:**
Something is holding port 8765:
```bash
lsof -ti tcp:8765 | xargs kill -9
```

### Symptom: Port never binds (start script times out)
SIMON started but something crashed during import. Check log for the actual error after the banner.

---

## SIMON Greets but Won't Respond to Requests

### Check 1: Is it a conversational or tool request?
Conversational requests (greetings, "what time is it") go to HQ. Tool requests go to Cloud. If HQ is offline and you say "good morning", SIMON will still respond via HQ timeout → Cloud fallback.

### Check 2: Check the log in real time
```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v huggingface
```
When you speak, you should see `[HQ] ✅ Response` or `[TOOL] toolname(...)` within a few seconds.

### Check 3: Is the Cloud API responding?
```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer YOUR_OLLAMA_CLOUD_KEY" \
  https://api.ollama.com/api/tags
```
Should return `200`. If not, check internet connection.

### Check 4: Is the WebSocket connected?
Open browser DevTools → Network → WS tab. You should see an active connection to `ws://localhost:8765/ws/...`. If not, SIMON's FastAPI server may have crashed.

---

## "Listening But Not Responding" (Historic — Fixed in v4.3)

This was the major bug in v4.2. Root cause: HQ received tool definitions and misrouted every request to `hq_ask`. Fixed by removing tools from HQ payload entirely.

If this recurs, check the log for:
```
[TOOL] hq_ask({"prompt": "[the user's actual message]"})
```
If you see this, the HQ routing logic has regressed. `_hq_chat_simple()` must NOT include tools in its payload.

---

## HQ Bridge "Not Reachable" at Startup

### v4.3 behavior: This is normal and non-blocking
The new v2.1 bridge logs `"Background handshake started (non-blocking)"`. SIMON continues to start normally. HQ comes online in the background.

### If HQ is genuinely offline:
```bash
# Check from Mac
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health

# SSH to HQ and check services
ssh user@YOUR_HQ_TAILSCALE_IP
sudo systemctl status simon-hq-api ollama
```

Common fixes on HQ:
```bash
sudo systemctl restart simon-hq-api
sudo systemctl restart ollama
```

### If Tailscale is the issue:
```bash
# On Mac
tailscale status
tailscale ping YOUR_HQ_TAILSCALE_IP

# On HQ
sudo tailscale up
```

---

## Vision Tools Not Working

### Camera not found
Check macOS camera permissions:
System Settings → Privacy & Security → Camera → Terminal = ON

Then restart SIMON.

### YOLO not loading
```bash
# Check if model file exists
ls -lh ~/Projects/AI-Projects/jarvis/yolo26n.pt

# If missing, re-download
cd ~/Projects/AI-Projects/jarvis
python3.11 -c "from ultralytics import YOLO; YOLO('yolo26n.pt')"
```

### Vision ask returning nothing
If HQ is online, check `/vision/ask` endpoint on HQ:
```bash
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health | python3 -m json.tool
```
Look for `llama3.2-vision` in the response.

---

## Messages Not Sending

### Check Messages.app is open
```bash
pgrep -x Messages && echo "Running" || (open -a Messages && echo "Opened")
```

### Check Full Disk Access
System Settings → Privacy & Security → Full Disk Access → Terminal = ON

Without Full Disk Access, SIMON cannot read the Messages SQLite database.

### Verify phone number format
SIMON normalizes to 10-digit format. Numbers like `+1(555)867-5309` get normalized to `5558675309`. If a contact has an unusual format, test manually with the `search_contacts` tool.

---

## Email Not Working

### Check Mail.app is open
```bash
pgrep -x Mail && echo "Running" || (open -a Mail && echo "Opened")
```

### Mail.app needs to be fully loaded
Mail.app takes 3-5 seconds to initialize accounts after opening. SIMON waits 3 seconds after auto-opening it, but if Mail is slow, the first email command may fail. Simply repeat the request.

---

## High RAM Usage

Target: ~10–11GB idle, ~13–14GB active.

If you see RAM climbing above 15GB:
1. Check if MLX loaded unexpectedly: `grep "MLX.*ready" ~/Projects/AI-Projects/jarvis/jarvis.log`
2. Check if Moondream loaded: `grep "Moondream.*loaded" ~/Projects/AI-Projects/jarvis/jarvis.log`
3. If both loaded unnecessarily, restart SIMON: `~/Projects/AI-Projects/jarvis/restart_simon.sh`

---

## TOKENIZERS Warnings Flooding the Log

These are harmless. They come from HuggingFace's tokenizer library detecting fork operations (which happen every time SIMON runs AppleScript or shell commands).

They're already set to false in the environment. To suppress from your log watching:
```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v "To disable" | grep -v huggingface
```

---

## Health Check Shows Too Many Issues

Expected DEGRADED items (not real problems):
- **MLX Fast Path** — intentionally not pre-loaded. Normal.
- **Vision (YOLO + Moondream)** — Moondream intentionally not pre-loaded. Normal.

Expected DOWN items that auto-heal:
- **Mail.app / Messages.app** — SIMON auto-opens them. Normal if SIMON just started.

Real problems worth investigating:
- **WiFi DOWN** — actual connectivity issue
- **Internet DOWN** — no outbound access
- **DNS DOWN** — DNS resolution failing
- **Piper TTS DOWN** — voice model missing
- **Knowledge Base DOWN** — SQLite file corrupted or permission issue

---

## Diagnosing Tool Failures

Enable verbose tool logging by watching:
```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep "\[TOOL\]"
```

Each tool call logs:
```
[TOOL] tool_name({"arg1": "value1"})
[TOOL] tool_name → result text
```

If you see `argument error` or `unexpected arguments`, the LLM sent malformed args. The sanitizer in v4.3 handles the most common patterns, but new patterns may appear. Log them and add to the sanitizer.
