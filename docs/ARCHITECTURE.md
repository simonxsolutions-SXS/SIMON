# Architecture — S.I.M.O.N.

> How S.I.M.O.N. is structured, how data flows, and why each component was built the way it was.

---

## High-Level Architecture

S.I.M.O.N. is a single-machine system with four major layers:

```
┌─────────────────────────────────────────────────────────────────┐
│  PRESENTATION LAYER                                              │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Chrome HUD (hud.html)                                    │  │
│  │                                                           │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │  │
│  │  │  Neural  │  │ System   │  │ Activity │  │  Chat   │  │  │
│  │  │  Brain   │  │ Vitals   │  │   Log    │  │  Box    │  │  │
│  │  │  Canvas  │  │  Panel   │  │  Panel   │  │         │  │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────┘  │  │
│  │                                                           │  │
│  │  Web Speech API (Chrome)  ←→  WebSocket (:8765/ws)       │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────────────┘
                          │ WebSocket (JSON frames)
┌─────────────────────────▼───────────────────────────────────────┐
│  CORE LAYER                                                      │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  S.I.M.O.N. Core Server  (jarvis.py)                      │  │
│  │                                                           │  │
│  │  FastAPI + Uvicorn                                        │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  Session Manager  │  Context Summarizer             │  │  │
│  │  │  (per WebSocket)  │  (gemma3:12b compression)       │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ┌───────────────┐     ┌─────────────────────────────┐   │  │
│  │  │  LLM Client   │     │   Tool Dispatcher           │   │  │
│  │  │  (httpx async)│     │   14 registered tools       │   │  │
│  │  │  Ollama API   │     │   non-blocking async        │   │  │
│  │  └───────────────┘     └─────────────────────────────┘   │  │
│  │                                                           │  │
│  │  ┌───────────────────────────────────────────────────┐   │  │
│  │  │  Piper TTS (Python API)  →  WAV  →  afplay        │   │  │
│  │  │  Fallback: macOS say -v Daniel                    │   │  │
│  │  └───────────────────────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────┬──────────────────────────┬────────────────────────┘
              │ async tool calls          │ KB queries
┌─────────────▼──────────┐  ┌────────────▼────────────────────────┐
│  INTEGRATION LAYER      │  │  DATA LAYER                         │
│                         │  │                                     │
│  Apple apps via         │  │  Local Knowledge Base               │
│  AppleScript:           │  │  (~/.simon-x/simon_kb.db)           │
│                         │  │                                     │
│  • Calendar.app         │  │  ┌─────────────────────────────┐   │
│  • Mail.app             │  │  │  contacts       — permanent  │   │
│  • Messages.app (send)  │  │  │  messages_cache — 48h TTL    │   │
│  • Reminders.app        │  │  │  memory         — permanent  │   │
│  • Contacts.app         │  │  │  email_senders  — permanent  │   │
│                         │  │  │  session_log    — 30d TTL    │   │
│  Direct SQLite reads:   │  │  └─────────────────────────────┘   │
│  • Messages chat.db     │  │                                     │
│  • AddressBook .abcddb  │  │  Self-healing maintenance:          │
│    (read-only, WAL)     │  │  • On startup                       │
│                         │  │  • Every 6h background task         │
└─────────────────────────┘  └─────────────────────────────────────┘
```

---

## Data Flow: Voice Command → Response

```
┌─────────┐
│  User   │  "Simon, check my messages"
└────┬────┘
     │
     ▼
┌────────────────────────────────────┐
│  Chrome Web Speech API             │
│  continuous=true, interimResults   │
│  Wake word detected: "simon"       │
│  → awake=true, conversing=true     │
└────────────┬───────────────────────┘
             │ WebSocket JSON: {type:"chat", text:"check my messages"}
             ▼
┌────────────────────────────────────┐
│  S.I.M.O.N. Core (jarvis.py)       │
│                                    │
│  1. Build system prompt            │
│     + inject KB memory context     │
│  2. POST /api/chat to Ollama       │
│     stream=false (tool detection)  │
│  3. Model returns tool_calls:      │
│     get_recent_messages(hours=24)  │
└────────────┬───────────────────────┘
             │ {type:"thinking"} → HUD shows spinner
             │ {type:"tool_use", tool:"get_recent_messages"}
             ▼
┌────────────────────────────────────┐
│  Tool: get_recent_messages()       │
│                                    │
│  1. Query messages_cache in KB     │
│     (sub-millisecond SQL)          │
│  2. If cache miss: scan chat.db    │
│     WAL via read-only URI          │
│  3. Resolve handles → names        │
│     from contacts cache (O(1))     │
│  4. Mark rows read_by_simon=1      │
│  5. Return formatted message list  │
└────────────┬───────────────────────┘
             │ tool result injected into context
             ▼
┌────────────────────────────────────┐
│  S.I.M.O.N. Core (streaming)       │
│                                    │
│  POST /api/chat stream=true        │
│  Model generates spoken response   │
│  Each chunk → WebSocket            │
│  {type:"chunk", text:"..."}        │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│  Piper TTS                         │
│                                    │
│  synthesize_wav(text) → WAV        │
│  afplay /tmp/simon_tts.wav         │
│  → {type:"speech_done"}            │
└────────────┬───────────────────────┘
             │ 800ms audio-settle window
             ▼
┌────────────────────────────────────┐
│  HUD switches to LISTENING state   │
│  Waveform activates (green)        │
│  Awaiting user response            │
└────────────────────────────────────┘
```

---

## Component Reference

### jarvis.py — Core Server

The heart of S.I.M.O.N. Runs as a FastAPI application on port 8765.

**Responsibilities:**
- WebSocket server for HUD communication
- LLM conversation management with context windowing
- Tool calling and dispatch
- Piper TTS synthesis and playback
- System stats collection (CPU, memory, disk, IP, load)
- Background KB sync scheduling

**Key design decisions:**

| Decision | Why |
|---|---|
| Non-blocking async tools | Tool calls (AppleScript, subprocess) can take 250ms+. Async prevents blocking the event loop and keeps the HUD responsive. |
| `stream=false` for tool detection | Ollama streams tool_call events unreliably. First pass is non-streaming to detect tools; second pass streams the spoken response. |
| Context summarizer (gemma3:12b) | Long conversations exceed context windows. A lightweight model compresses old history while preserving key facts. |
| 800ms post-speech settle window | Chrome's SR picks up TTS audio bleed-through. The window discards buffered audio before re-enabling `awake=true`. |

### hud.html — Browser HUD

A single HTML file running in Chrome. Communicates via WebSocket.

**State machine:**

```
sleeping ──wake word──► wake ──speech──► listening ──text──► processing ──stream──► speaking
   ▲                     │                                                               │
   │                     │◄──────────────────── speech_done (800ms delay) ──────────────┘
   └──── kill phrase ─────┘
```

**Neural brain canvas:**
The animated neural network is a canvas element with 52 nodes arranged in 5 clusters. Node color, packet speed, and glow intensity respond to the current state — idle (dim blue), listening (green), processing (gold/amber), speaking (bright blue), muted (red).

### simon_kb.py — Knowledge Base

Local SQLite database at `~/.simon-x/simon_kb.db`.

**Schema design principles:**
- `contacts`: ONE row per person. Name is the `UNIQUE` key. Phone and email on the same row. No duplicates by design.
- `messages_cache`: Short-lived buffer only. Every row has `expires_at`. Rows are flagged `read_by_simon=1` when SIMON reads them, then cleared on next maintenance.
- `memory`: Permanent key-value store. Survives all restarts. Only grows intentionally.
- `session_log`: Rolling 30-day window. Pruned automatically.

### AppleScript Integration

Tools that need to write to Apple apps (send message, create event) use AppleScript via `osascript`. Read operations bypass AppleScript entirely:

| Operation | Method | Why |
|---|---|---|
| Read messages | Direct SQLite (chat.db WAL) | AppleScript hangs on bulk reads |
| Read contacts | Direct SQLite (AddressBook .abcddb) | 250ms vs <1ms |
| Read emails | AppleScript (Mail.app) | No public SQLite path |
| Send message | AppleScript (Messages.app) | Required for write access |
| Create event | AppleScript (Calendar.app) | Required for write access |

### launchd Scheduling

| Agent | Schedule | Purpose |
|---|---|---|
| `com.yourname.simon` | On login + crash-restart | Keep S.I.M.O.N. running |
| `com.yourname.healthcheck.morning` | 7:45 AM | Morning system report |
| `com.yourname.healthcheck.afternoon` | 3:00 PM | Afternoon system report |
| `com.yourname.healthcheck.evening` | 9:00 PM | Evening system report |
| `com.yourname.healthcheck.catchup` | On login | Catch missed checks after sleep/reboot |

---

## Memory & Context Management

### LLM Context Window

```
┌────────────────────────────────────────────────┐
│  [SYSTEM PROMPT]                                │
│  • SIMON personality + speech rules             │
│  • Current time/date                           │
│  • Injected KB memory (all permanent facts)    │
├────────────────────────────────────────────────┤
│  [CONTEXT SUMMARY] (if history > 24 msgs)      │
│  • 2-4 sentence compression by gemma3:12b      │
├────────────────────────────────────────────────┤
│  [CONVERSATION HISTORY]                         │
│  • Last 16 turns verbatim                      │
│  • user / assistant / tool messages            │
└────────────────────────────────────────────────┘
```

Compression fires at 24 messages, keeps 16 recent turns verbatim.

### KB Memory Injection

Every session starts with all `memory` table entries injected into the system prompt:

```
PERSISTENT MEMORY (stored locally, survives all restarts):
[person] wife_contact: [name from contacts]
[preference] trading_platform: your preferred platform
[fact] machine_specs: MacBook Air, 16GB RAM, 512GB
...
```

This means SIMON walks into every conversation already knowing everything you've ever told it.

---

## Performance Profile

| Operation | Before KB | After KB |
|---|---|---|
| Contact name lookup | 250ms (AppleScript) | <1ms (SQL index) |
| Message scan (24h) | 50-200ms (WAL scan + resolve) | <5ms (cache hit) |
| System stats | 15ms (top + vm_stat) | 15ms (unchanged) |
| LLM inference | 1-8s (network, model dependent) | 1-8s (unchanged) |
| TTS synthesis | 200-800ms (Piper, text length) | 200-800ms (unchanged) |

---

## Security Model

S.I.M.O.N. assumes it runs on a personal, trusted machine. It is not designed for multi-user or server deployment.

| Data | Storage | Access |
|---|---|---|
| Contacts | `~/.simon-x/simon_kb.db` | Local only |
| Messages cache | `~/.simon-x/simon_kb.db` | Local, 48h TTL |
| Permanent memory | `~/.simon-x/simon_kb.db` | Local only |
| Config (API keys) | `config.json` | Local only, gitignored |
| LLM API key | `config.json` | Sent only to your Ollama endpoint |
| Apple data | Never written to KB permanently | Read-only, ephemeral |

`config.json` is listed in `.gitignore` and must never be committed.
