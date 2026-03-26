# S.I.M.O.N. — Systems Intelligence & Management Operations Node
**Simon-X Solutions | [OWNER_NAME] | v4.1**

A fully local personal AI assistant with a voice interface, neural brain HUD, conversation mode, and live integrations with email, calendar, iMessage, reminders, shell, and system monitoring. Powered by Ollama Cloud (gemma3:27b) and Piper neural TTS.

---

## Quick Start

```bash
# First-time setup (run once)
bash ~/Projects/AI-Projects/jarvis/install_simon.sh

# Launch SIMON (every time)
bash ~/Projects/AI-Projects/jarvis/start_simon.sh
```

Opens automatically in **Google Chrome** at **http://localhost:8765**.

Say **"Simon"** to activate. SIMON stays in conversation mode — keep talking naturally. Say a kill phrase to pause.

---

## Project Structure

```
~/Projects/AI-Projects/jarvis/
├── jarvis.py            ← Main FastAPI server (S.I.M.O.N. backend, v4.1)
├── hud.html             ← Browser HUD (neural brain UI, conversation mode)
├── config.json          ← API keys and settings (chmod 600)
├── start_simon.sh       ← Launch script (opens Chrome, single window)
├── install_simon.sh     ← One-time installer (deps + voice model)
├── start_jarvis.sh      ← Legacy launcher (deprecated)
├── jarvis.log           ← Runtime log (auto-generated)
├── launchd_simon.log    ← LaunchAgent log (auto-generated)
├── github/              ← GitHub-ready public documentation
└── voices/
    ├── en_GB-alan-medium.onnx       ← Piper TTS voice model (60MB)
    └── en_GB-alan-medium.onnx.json  ← Voice model config
```

---

## System Architecture

```
Browser (Google Chrome)
  ├─ hud.html
  │    ├─ Web Speech API (wake word + conversation mode)
  │    ├─ Neural brain canvas (52-node animated graph)
  │    └─ WebSocket client ↔ jarvis.py
  └─ http://localhost:8765

jarvis.py (FastAPI + uvicorn, port 8765)
  ├─ WebSocket /ws/{session_id}  ← Chat, ping, stop, clear
  ├─ GET /                       ← Serves hud.html
  ├─ GET /api/status             ← CPU/RAM/Disk snapshot (df -H)
  ├─ GET /api/disk               ← Disk usage detail
  ├─ GET /api/emails             ← Unread counts via Mail.app
  └─ GET /api/calendar           ← Today's events via Calendar.app

Ollama Cloud (api.ollama.com)
  └─ Model: gemma3:27b           ← LLM inference (tool-calling + streaming)

Piper TTS (local, offline)
  └─ Voice: en_GB-alan-medium    ← Neural British male voice (Alan)
       └─ Output: /tmp/simon_tts.wav → afplay

macOS Integrations (AppleScript / SQLite)
  ├─ Calendar.app     ← Create events, read today/upcoming
  ├─ Messages.app     ← Send iMessage/SMS, read conversations
  ├─ Mail.app         ← Send email, read unread inbox
  ├─ Reminders.app    ← Create and read reminders
  ├─ Contacts.app     ← Search contacts by name
  └─ Shell            ← Run safe system commands
```

---

## Voice Flow (How It Works)

```
1. SIMON starts → hud.html loads → SpeechRecognition starts (1 persistent instance)
2. State: MONITORING — listening continuously for wake word
3. You say "Simon" → awake=true, conversing=true → state: LISTENING
4. You speak your command → final transcript captured, wake word stripped
5. Command sent to jarvis.py via WebSocket
6. jarvis.py calls Ollama Cloud (gemma3:27b) with tool definitions
7. If tool call detected: executes macOS integration → streams spoken confirmation
8. Full response piped to Piper → generates .wav → afplay plays it
9. speech_done received → if conversing=true: awake=true (no wake word needed)
10. You speak next command → immediately processed
11. Say a kill phrase → conversing=false → back to MONITORING
```

Key design: **one SpeechRecognition instance, never destroyed.** `continuous: true` with
`awake` and `conversing` flags gates speech processing. Eliminates mic flicker.

---

## Conversation Mode

After saying **"Simon"** once, SIMON enters **conversation mode** — continue talking
naturally without repeating the wake word.

| Trigger | Behavior |
|---------|----------|
| Say **"Simon"** | Activates, enters conversation mode |
| Speak any command | Processed immediately (no wake word needed) |
| SIMON responds | Stays ready — "CONVERSATION MODE — SPEAK FREELY" |
| **90 seconds idle** | Auto-exits conversation mode |
| Say a **kill phrase** | Immediately exits conversation mode |
| Hit **MUTE** | Exits conversation mode, mutes mic |

### Kill Phrases

- "Give me a second" / "Give me a moment"
- "I'll be right back"
- "Stand by" / "Standby"
- "That's all"
- "Go to sleep"
- "Stop listening"
- "Goodbye Simon"

---

## MCP Tool Calling

| Tool | Voice Example | Action |
|------|---------------|--------|
| `create_calendar_event` | "Simon, schedule a meeting Monday at 2 PM" | Creates Calendar.app event |
| `get_todays_events` | "Simon, what's on my calendar today?" | Reads today's schedule |
| `get_upcoming_events` | "Simon, what do I have this week?" | Reads next N days |
| `send_imessage` | "Simon, text John I'll be 5 minutes late" | Sends iMessage/SMS |
| `read_imessages` | "Simon, read my last messages from Madeline" | Reads conversation |
| `send_email` | "Simon, email the client the proposal is ready" | Sends via Mail.app |
| `get_unread_emails` | "Simon, do I have urgent emails?" | Reads unread inbox |
| `create_reminder` | "Simon, remind me to call Dr. Smith at 9 AM" | Creates Reminders.app entry |
| `get_reminders` | "Simon, what are my pending reminders?" | Lists open reminders |
| `run_shell` | "Simon, what's my IP address?" | Runs safe shell command |
| `search_contacts` | "Simon, what's John's phone number?" | Searches Contacts.app |
| `get_system_status` | "Simon, system check" | CPU, RAM, disk usage |

---

## Wake Word Sensitivity

The HUD includes a **WAKE SENSITIVITY** slider in the SYSTEMS panel.

| Setting | Behavior |
|---------|----------|
| **Left (Sensitive, 0.0)** | Triggers on any speech containing "Simon" |
| **Center (0.55, default)** | Requires reasonably clear pronunciation |
| **Right (Strict, 1.0)** | Only very high-confidence "Simon" |

Live — no page reload needed. Adjustments logged in Activity Log.

---

## LaunchAgent (Auto-Start)

SIMON starts on login and restarts on crash.

```
~/Library/LaunchAgents/com.simonx.simon.plist
```

Settings: `RunAtLoad: true`, `KeepAlive → Crashed: true`, `ThrottleInterval: 15s`.
Runs `python3.11 jarvis.py` directly so launchd owns the PID.

```bash
# Restart
launchctl kickstart -k gui/$(id -u)/com.simonx.simon

# Stop
launchctl kill SIGTERM gui/$(id -u)/com.simonx.simon

# Status
launchctl list com.simonx.simon

# Logs
tail -f ~/Projects/AI-Projects/jarvis/jarvis.log
```

---

## Context Summarization

| Setting | Value |
|---------|-------|
| Trigger | 24 messages in history |
| Keep recent | 16 most recent turns (verbatim) |
| Summarizer model | gemma3:12b (fast, lightweight) |
| Preserves | Completed tasks, decisions, key facts |
| Fallback | Hard trim to 16 if summarization fails |
| Double-compression | Skipped — existing summaries never re-compressed |

---

## Configuration (config.json)

```json
{
  "ollama_cloud_key": "your-api-key",
  "ollama_cloud_url": "https://api.ollama.com",
  "model": "gemma3:27b",
  "port": 8765
}
```

File is `chmod 600`. **Never commit to git.**

Available Ollama Cloud models: gemma3:27b, gemma3:12b, devstral-2:123b,
qwen3-next:80b, mistral-large-3:675b, deepseek-v3.2, kimi-k2:1t, and more.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves hud.html |
| `/ws/{session_id}` | WebSocket | Chat + control channel |
| `/api/status` | GET | CPU, RAM, disk, time, date |
| `/api/disk` | GET | Disk detail (df -H decimal units) |
| `/api/emails` | GET | Unread counts per account |
| `/api/calendar` | GET | Today's calendar events |

### WebSocket Messages

**Client → Server:**

| Type | Payload | Action |
|------|---------|--------|
| `ping` | — | Request stats |
| `chat` | `{text}` | Send command to SIMON |
| `stop` | — | Interrupt speech |
| `clear` | — | Reset session |

**Server → Client:**

| Type | Payload | Meaning |
|------|---------|--------|
| `greeting` | `{text}` | Initial greeting |
| `stats` | `{cpu, mem_gb, disk_used, disk_avail, disk_pct, time, date}` | System snapshot |
| `thinking` | — | LLM call started |
| `tool_use` | `{tool}` | Tool executing |
| `chunk` | `{text}` | Streaming response chunk |
| `done` | `{text}` | Response complete |
| `speech_done` | — | TTS finished |
| `cleared` | — | Session reset confirmed |

---

## Brain HUD States

| State | Color | Meaning |
|-------|-------|---------|
| `sleeping` | Dark teal | Initializing / disconnected |
| `wake` | Blue | Monitoring (or conversation mode ready) |
| `listening` | Bright green | Capturing command |
| `processing` | Gold | Calling LLM / executing tools |
| `speaking` | Cyan | TTS playing |
| `muted` | Dark red | Mic muted |

Conversation mode indicator: hint text reads **"CONVERSATION MODE — SPEAK FREELY"**
when `conversing=true` and state is `wake`.

---

## Email Accounts

| Account | HUD Label |
|---------|-----------|
| your@email.com | SimonX |
| your@email.com | Fixit |
| your@email.com | Personal |
| your@icloud.com | iCloud (optional) |

Counts auto-refresh every 90 seconds.

---

## Troubleshooting

### SIMON doesn't respond to voice
1. Check Activity Log for errors
2. If `⚠️ Mic blocked`: Chrome Settings → Site Settings → allow mic for `localhost:8765`
3. Must use **Google Chrome**
4. Lower the WAKE SENSITIVITY slider if wake word isn't triggering

### Server won't start
```bash
tail -30 ~/Projects/AI-Projects/jarvis/jarvis.log
lsof -i :8765
pkill -f jarvis.py && launchctl kickstart -k gui/$(id -u)/com.simonx.simon
```

### Tool calls not working
- Calendar: System Settings → Privacy → Calendars → allow Terminal
- iMessage read: System Settings → Privacy → Full Disk Access → add Terminal
- All tool calls logged: `grep "\[TOOL\]" jarvis.log | tail -20`

### Conversation mode drops after response
- Activity Log should show "Ready — continue talking"
- If showing "monitoring" instead, reload the page

---

## Dependencies

| Dependency | Version | Purpose |
|------------|---------|--------|
| Python | 3.11+ | Runtime |
| FastAPI | latest | Web/WS server |
| uvicorn | latest | ASGI server |
| httpx | latest | Async HTTP |
| piper-tts | latest | Neural TTS |
| afplay | macOS built-in | Audio playback |
| osascript | macOS built-in | App integrations |
| Chrome | any | Web Speech API |

---

## Build History

| Version | Key Changes |
|---------|-------------|
| v1.0 | Initial JARVIS build. FastAPI + WebSocket. macOS `say` TTS. Local llama3.2:3b. |
| v1.1 | Switched to Daniel (en_GB) voice. TTS sync fix. `speech_done` added. |
| v1.2 | Mic flicker fix: `continuous: true`. State machine. Interim transcript. |
| v2.0 | Renamed S.I.M.O.N. Wake word architecture. Reed voice. Neural brain canvas. |
| v3.0 | Single persistent mic. Ollama Cloud (gemma3:27b). Piper TTS (Alan, British). |
| v4.0 | Full MCP tool calling (Calendar, iMessage, Mail, Reminders, Shell, Contacts). Context summarizer. LaunchAgent fixed. Wake sensitivity slider. Disk widget (df -H). Chrome launch. Single browser window. Port cleanup at startup. |
| v4.1 | Conversation mode — stays listening after each response. Kill phrases. 90-second idle timeout. "CONVERSATION MODE — SPEAK FREELY" HUD indicator. |

---

## Security Notes

- `config.json` is `chmod 600` — only your user can read it
- API key never sent to browser, never logged
- All LLM calls go over HTTPS to `api.ollama.com`
- Email/calendar/iMessage data stays local, never sent externally
- Shell tool blocks destructive commands (`rm -rf`, `dd`, `shutdown`, etc.)
- HUD binds to `0.0.0.0:8765` — change to `127.0.0.1` in jarvis.py for localhost-only

---

## Backlog

- [ ] Multiple voice options selectable from HUD
- [ ] Secure bind to `127.0.0.1` only
- [ ] Apple Notes integration
- [ ] Offline fallback to local Ollama model
- [ ] MCP hot-reload

---

*Simon-X Solutions*
