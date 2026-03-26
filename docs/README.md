# S.I.M.O.N. Documentation
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.3 | Last Updated: March 21, 2026**

---

## Documents

| File | Description |
|---|---|
| `README.md` | This index |
| `CHANGELOG.md` | Full version history — what changed in each version |
| `RELEASE_NOTES_v4.2_to_v4.3.md` | Deep-dive on every fix and upgrade in this session |
| `ARCHITECTURE.md` | Full system architecture — Mac, HQ, routing, services, background tasks |
| `OPERATIONS.md` | How to start, stop, restart, monitor, and maintain SIMON |
| `TOOLS_REFERENCE.md` | Every tool SIMON can use, with trigger phrases and security notes |
| `NETWORK.md` | Network config, Tailscale, IPs, ports, services, PostgreSQL |
| `KNOWN_ISSUES.md` | Active bugs, expected non-issues, resolved issues, roadmap |
| `TROUBLESHOOTING.md` | Step-by-step diagnosis for every common failure mode |

---

## Quick Reference

### Start / Stop / Restart

```bash
~/Projects/AI-Projects/jarvis/start_simon.sh
~/Projects/AI-Projects/jarvis/stop_simon.sh
~/Projects/AI-Projects/jarvis/restart_simon.sh
```

### Watch the Log

```bash
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log | grep -v TOKENIZERS | grep -v "To disable" | grep -v huggingface
```

### Check if Running

```bash
pgrep -f jarvis.py && echo "RUNNING" || echo "STOPPED"
```

### HUD
**http://localhost:8765**

---

## System at a Glance

```
Mac (M5 MacBook Air, 24GB)          simon-hq (Ubuntu i7, 33GB)
  SIMON FastAPI :8765           ←→    HQ API :8200
  Piper TTS Alan voice                llama3.1:8b  (conversational)
  YOLO26n camera detection            llama3.2-vision:11b (vision Q&A)
  AppleScript tools (21)              ChromaDB :8100 (vector memory)
  macOS security gate                 PostgreSQL :5432 (relational DB)
  Web Speech API (mic)                Ollama :11434 (local LLM host)
       ↓
  Ollama Cloud
  mistral-large-3:675b
  (ALL tool calls)
```

### LLM Routing (v4.3)

| Request Type | Goes To | Latency |
|---|---|---|
| Greetings, small talk | HQ llama3.1:8b (no tools) | ~1s |
| Tool calls (calendar, email, etc.) | Cloud Mistral Large | ~3–8s |
| HQ offline 60s+ | MLX Mistral 7B (emergency) | ~2s |

---

## Current Version — v4.3

**Date:** March 21, 2026

**Key changes from v4.2:**
- Fixed "listening but not responding" — HQ no longer receives tool definitions
- Fixed startup hang — HQ Bridge is now non-blocking
- Fixed Python 3.11 syntax error in vision_detect
- RAM reduced from ~17GB to ~10–11GB idle
- Auto-heal for Mail.app and Messages.app
- Tool argument sanitizer for malformed LLM responses
- PATH fix for ifconfig and network tools
- Start/stop/restart scripts with syntax pre-check

See `RELEASE_NOTES_v4.2_to_v4.3.md` for full details.

---

## File Locations

| Item | Path |
|---|---|
| Main backend | `~/Projects/AI-Projects/jarvis/jarvis.py` |
| Config | `~/Projects/AI-Projects/jarvis/config.json` |
| Log | `~/Projects/AI-Projects/jarvis/jarvis.log` |
| HUD | `~/Projects/AI-Projects/jarvis/hud.html` |
| Plugins | `~/Projects/AI-Projects/jarvis/plugins/` |
| Vision | `~/Projects/AI-Projects/jarvis/vision/` |
| Voices | `~/Projects/AI-Projects/jarvis/voices/` |
| KB (SQLite) | `~/.simon-x/simon_kb.db` |
| LaunchAgent | `~/Library/LaunchAgents/com.simonx.simon.plist` |
| Scripts | `~/Projects/AI-Projects/jarvis/start_simon.sh` |
| Docs | `~/Projects/AI-Projects/jarvis/docs/` |
