# S.I.M.O.N. User Repair Guide
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.5 | March 21, 2026**

> This is YOUR guide — written in plain language, no engineering degree required.
> For the full technical runbook, see `HQ_REPAIR_RUNBOOK.md`.
> For HQ and LM Studio AI-assisted repair, see the bottom of this guide.

---

## "Simon isn't responding at all"

**Step 1 — Check if SIMON is running:**
```bash
lsof -ti tcp:8765 && echo "SIMON is running" || echo "SIMON is NOT running"
```

**Step 2 — If not running, start him:**
```bash
cd ~/Projects/AI-Projects/jarvis && ./start_simon.sh
```

**Step 3 — If the start script shows a SYNTAX ERROR:**
Take a screenshot of the error and bring it here. Don't try to fix jarvis.py manually — it'll make it worse.

**Step 4 — If he starts but still doesn't respond:**
```bash
tail -30 ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v huggingface
```
Look for any line with `❌` or `Error` and paste it here.

---

## "Simon started but won't greet me"

This is intentional — SIMON waits 10 seconds after the browser connects before greeting. He's doing his system check. Give it 10-15 seconds. If still nothing after 30 seconds:

1. Refresh the browser tab (`Cmd+R`)
2. If still silent, check the log: `tail -20 ~/Projects/AI-Projects/jarvis/jarvis.log`

---

## "Simon can't send texts to [contact name]"

SIMON sends personal texts via the Pixel 9a (Android), not iMessage. If texts fail:

**Check ADB connection:**
```bash
adb devices
```
Should show `YOUR_ANDROID_TAILSCALE_IP:5555   device`. If it shows "offline" or nothing:
```bash
adb kill-server && adb start-server
adb connect YOUR_ANDROID_TAILSCALE_IP:5555
```
Then **tap Allow on your Pixel 9a** when the dialog appears.

**Ask SIMON to reconnect:**
> "Simon, reconnect to my Android"

---

## "Simon's texts don't show up in Google Messages"

This was fixed in v4.5 — messages now write to the sent box automatically. If you're still seeing this:

1. Make sure SIMON was restarted after March 21, 2026
2. Say: "Simon, send a test message to My Love saying test"
3. Check Google Messages → should appear in sent

---

## "Simon keeps saying he can't reach HQ"

HQ (simon-hq) might be offline or Tailscale dropped.

**Quick check:**
```bash
curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health | python3.11 -m json.tool | grep status
```

If this fails:
```bash
# Check Tailscale
tailscale ping YOUR_HQ_TAILSCALE_IP
```

If Tailscale is down, open the Tailscale menu bar app and reconnect. SIMON still works without HQ — he just uses the Cloud model for everything. It's slower but functional.

---

## "Simon isn't doing system check / health looks wrong"

Some things always show as DEGRADED — this is normal and not a problem:
- **MLX Fast Path** — not pre-loaded by design
- **Vision (Moondream)** — not pre-loaded by design
- **Mail.app / Messages.app** — briefly DOWN right after startup, then SIMON auto-opens them

Real problems to investigate:
- **WiFi DOWN** — check your Mac's WiFi
- **Piper TTS DOWN** — means SIMON's voice model is missing (he'll still talk, just different voice)
- **Knowledge Base DOWN** — run the healer (see below)

---

## "Simon won't stop talking / audio stuck"

Click the **STOP** button in the HUD, or say "Stop" clearly.

If audio is truly stuck (not responding to stop):
```bash
pkill -9 afplay
pkill -9 say
```

---

## "Port 8765 already in use" error

Something is holding SIMON's port. Kill it:
```bash
lsof -ti tcp:8765 | xargs kill -9
```
Then start SIMON normally.

---

## "LM Studio not responding"

LM Studio is a manual-start app on simon-hq — it doesn't auto-start.

1. Log into simon-hq (Remote Desktop or SSH)
2. Open LM Studio
3. Load the Mistral 7B model
4. Click the **API** tab on the left → click **Start Server**
5. Say "Simon, LM Studio status" to confirm

---

## Running the Self-Repair Engine

SIMON has a built-in repair tool. Run it anytime:

```bash
cd ~/Projects/AI-Projects/jarvis
python3.11 simon_healer.py
```

It checks everything and auto-fixes what it can. Output example:
```
[SIMON Healer] Running diagnosis...
  ✅ Mail.app            — running
  ✅ Messages.app        — running
  ✅ Port 8765           — free
  ✅ KB Integrity        — ok
  ❌ Piper TTS           — binary not found at ~/.local/bin/piper
  ✅ Log File Size       — 2.1MB (ok)
  ✅ ADB Connectivity    — YOUR_ANDROID_TAILSCALE_IP:5555 device

[SIMON Healer] 1 issue found.
  → Piper TTS: Download from https://github.com/rhasspy/piper/releases
```

For diagnosis only (no changes made):
```bash
python3.11 simon_healer.py --diagnose-only
```

---

## Asking HQ to Help Repair SIMON

The HQ model (llama3.1:8b on simon-hq) knows your entire system — every issue, every fix. You can ask it directly:

```bash
python3.11 simon_healer.py --ask-hq "describe the error you're seeing here"
```

Example:
```bash
python3.11 simon_healer.py --ask-hq "jarvis.py keeps crashing with ModuleNotFoundError: mlx_lm"
```

HQ will give you the exact fix command.

---

## Asking LM Studio to Help Repair SIMON

LM Studio's chat UI has the SIMON system prompt loaded. Open LM Studio on simon-hq, go to the chat tab, and just ask:

> "Simon's health check shows Knowledge Base DOWN. What do I do?"
> "What does 'adb unauthorized' mean and how do I fix it?"
> "SIMON started but isn't playing voice — what are the possible causes?"

LM Studio knows your full setup and will give you targeted answers.

---

## Quick Command Reference

| Problem | Command |
|---------|---------|
| Start SIMON | `cd ~/Projects/AI-Projects/jarvis && ./start_simon.sh` |
| Restart SIMON | `cd ~/Projects/AI-Projects/jarvis && ./restart_simon.sh` |
| Run self-repair | `python3.11 simon_healer.py` |
| Watch live log | `tail -f ~/Projects/AI-Projects/jarvis/jarvis.log \| grep -v TOKENIZERS` |
| Kill port conflict | `lsof -ti tcp:8765 \| xargs kill -9` |
| Reconnect Android | `adb connect YOUR_ANDROID_TAILSCALE_IP:5555` |
| Kill stuck audio | `pkill -9 afplay && pkill -9 say` |
| Check HQ health | `curl -s http://YOUR_HQ_TAILSCALE_IP:8200/health \| python3.11 -m json.tool` |
| Reload HQ brain | `curl -X POST http://YOUR_HQ_TAILSCALE_IP:8200/reload_brain` |
| SSH to simon-hq | `ssh simon-hq` |

---

## When to Just Restart SIMON

When in doubt — restart. It fixes 80% of issues:
```bash
cd ~/Projects/AI-Projects/jarvis && ./restart_simon.sh
```

SIMON auto-heals Mail.app, Messages.app, ADB, and port conflicts on every startup.

---

*Last updated: March 21, 2026 — S.I.M.O.N. v4.5*
