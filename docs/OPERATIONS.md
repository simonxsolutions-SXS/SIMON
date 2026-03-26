# S.I.M.O.N. Operations Guide
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.3 | March 21, 2026**

---

## Starting SIMON

```bash
~/Projects/AI-Projects/jarvis/start_simon.sh
```

What it does:
1. Kills any running jarvis.py, afplay, say processes
2. Clears port 8765
3. Runs Python syntax check — exits cleanly if there's a code error
4. Launches SIMON with correct environment variables
5. Waits up to 20 seconds for the server to bind
6. Opens Google Chrome to http://localhost:8765

---

## Stopping SIMON

```bash
~/Projects/AI-Projects/jarvis/stop_simon.sh
```

---

## Restarting SIMON

```bash
~/Projects/AI-Projects/jarvis/restart_simon.sh
```

---

## Watching the Log

```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v "To disable" | grep -v huggingface
```

---

## Healthy Startup Sequence

A clean boot should produce these lines in order:

```
[Vision] Module loaded — MPS ready
[MLX] Module loaded — on-demand only (HQ is primary)
[Health] Monitor loaded — proactive tool checks enabled
[HQ Bridge] Background handshake started (non-blocking)
[KB] Syncing and running maintenance on startup...
[Security] Guard active | 75 shell patterns | send scanning ON
[TTS] Piper loaded: ...voices/en_GB-alan-medium.onnx (sr=22050)
[RAM] Detected 24GB via /usr/sbin/sysctl
[Vision] Pre-warming YOLO26n...
[Vision] ✅ YOLO ready — Moondream on-demand only
[HQ] ✅ Online — fast responses via llama3.1:8b
[Health] 10 UP | 2 DEGRADED | 0 DOWN
```

The two DEGRADED items (MLX Fast Path, Moondream) are expected and normal — they are intentionally not pre-loaded.

---

## Manual LaunchAgent Control

If you need to use launchctl directly:

```bash
# Load (enables auto-start and starts now)
launchctl load ~/Library/LaunchAgents/com.simonx.simon.plist

# Unload (disables auto-start and stops now)
launchctl unload ~/Library/LaunchAgents/com.simonx.simon.plist

# Kick a fresh start (force restart even if already running)
launchctl kickstart -k gui/$(id -u)/com.simonx.simon
```

---

## Checking if SIMON is Running

```bash
pgrep -f jarvis.py && echo "RUNNING" || echo "STOPPED"
```

Or check the port:
```bash
lsof -ti tcp:8765 && echo "PORT OPEN" || echo "PORT FREE"
```

---

## simon-hq Service Control

SSH in first:
```bash
ssh user@YOUR_HQ_TAILSCALE_IP
```

Then:
```bash
# Check all services
sudo systemctl status simon-hq-api simon-chroma ollama postgresql

# Restart HQ API
sudo systemctl restart simon-hq-api

# View HQ API log
sudo journalctl -u simon-hq-api -f

# Check Ollama models
ollama list

# Check which models are loaded in RAM
curl -s http://localhost:11434/api/ps | python3 -m json.tool
```

---

## Checking HQ Health from Mac

```bash
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health | python3 -m json.tool
```

Expected response:
```json
{
  "status": "ok",
  "ollama": true,
  "chromadb": true,
  "cpu_pct": 2.1,
  "ram_used_gb": 3.0,
  "ram_total_gb": 33.4,
  "models_warm": ["llama3.1:8b"],
  "uptime_hours": 2.5
}
```

---

## Clearing the Log

```bash
> ~/Projects/AI-Projects/jarvis/jarvis.log
```

---

## Memory and KB

```bash
# View all stored memory facts (SQLite)
sqlite3 ~/.simon-x/simon_kb.db "SELECT category, key, value FROM memory ORDER BY created_at DESC;"

# View session history
sqlite3 ~/.simon-x/simon_kb.db "SELECT * FROM session_log ORDER BY started_at DESC LIMIT 10;"

# KB file size
ls -lh ~/.simon-x/simon_kb.db
```

---

## HUD Access

SIMON's browser interface: **http://localhost:8765**

The HUD shows:
- Live chat with SIMON
- System stats (CPU, RAM, disk, IP)
- Calendar events widget
- Email counts per account
- Packet travel panel (HUD → HQ hop timing)
- Tool use events in real time

---

## Config File

Path: `~/Projects/AI-Projects/jarvis/config.json`

Key fields:
```json
{
  "model": "mistral-large-3:675b",
  "port": 8765,
  "hq_api_url": "http://YOUR_HQ_TAILSCALE_IP:8200",
  "hq_model": "llama3.1:8b",
  "owner_name": "Your Name",
  "notification_phone": "+1XXXXXXXXXX",
  "owner_email": "your@email.com"
}
```

Never run SIMON without config.json present — it will crash at startup.
