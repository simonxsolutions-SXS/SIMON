# S.I.M.O.N. System Architecture
**Simon-X Solutions | [OWNER_NAME]**
**Version: 4.5 | March 21, 2026**

---

## Overview

S.I.M.O.N. is a two-machine unified intelligence system. The Mac is the voice, face, and hands. simon-hq is the brain and muscle. They operate as one.

```
┌─────────────────────────────────┐     Tailscale VPN      ┌──────────────────────────────────┐
│     Mac (M5 MacBook Air)        │ ←──────────────────── → │         simon-hq (Ubuntu)        │
│                                 │   YOUR_MAC_TAILSCALE_IP          │   YOUR_HQ_TAILSCALE_IP / YOUR_HQ_LOCAL_IP  │
│  • FastAPI on :8765             │   ←→ :8200 HQ API        │                                  │
│  • Piper TTS (Alan voice)       │   ←→ :1234 LM Studio     │  • FastAPI on :8200  (v2.2)      │
│  • YOLO26n camera detection     │                          │  • Ollama on :11434              │
│  • AppleScript tools            │                          │  • LM Studio on :1234            │
│  • Android bridge (ADB)         │                          │  • ChromaDB on :8100             │
│  • Web Speech API (mic)         │                          │  • PostgreSQL on :5432           │
│  • HUD browser interface        │                          │  • 33.4GB RAM / i7 CPU / NVIDIA  │
│  • Auto-boot via LaunchAgent    │                          │  • 982GB disk                    │
└─────────────────────────────────┘                          └──────────────────────────────────┘
                │                                                          │
                │                                     ┌────────────────────┘
                ▼                                     ▼
     Ollama Cloud API                        simon-hq Models:
     mistral-large-3:675b                    • llama3.1:8b — HQ conversational (warm)
     (ALL tool calls)                        • llama3.2-vision:11b — vision analysis
                                             • mistral-7b-instruct-v0.3 — LM Studio GPU
                                             • nomic-embed-text — embeddings
                                             • llama3.2:3b, phi3:mini, mistral:latest

                                    Android (Pixel 9a):
                                    • Tailscale: YOUR_ANDROID_TAILSCALE_IP:5555
                                    • WiFi: YOUR_ANDROID_LOCAL_IP:5555
                                    • ADB over WiFi, auto-failover
```

---

## LLM Routing (v4.5 Three-Tier)

### Decision Flow

```
User speaks
     │
     ▼
_is_conversational()?  ──YES──→  HQ llama3.1:8b (no tools) ──→ ~1s response
     │
    NO
     │
     ▼
_needs_tools()?  ──YES──→  Ollama Cloud Mistral Large (with all tools) ──→ executes
     │
    NO
     │
     ▼
HQ online?  ──YES──→  HQ llama3.1:8b (no tools) ──→ conversational response
     │
    NO
     │
     ▼
MLX loaded?  ──YES──→  Mistral 7B local (emergency fast path)
     │
    NO
     │
     ▼
Cloud fallback  ──→  Mistral Large (no tools available)
```

### Tier Details

**Tier 1 — HQ Conversational**
- Model: `llama3.1:8b` on simon-hq
- Endpoint: `POST http://YOUR_HQ_TAILSCALE_IP:8200/llm/chat`
- NO tools sent in payload
- Triggers: greetings, small talk, simple questions with no tool keywords
- Latency: ~1–2 seconds

**Tier 2 — Cloud Tool Execution**
- Model: `mistral-large-3:675b` via `https://api.ollama.com`
- ALL 21 core tools + plugin tools sent
- Triggers: any request needing calendar, messages, email, vision, shell, etc.
- Latency: ~3–8 seconds depending on tool complexity

**Tier 3 — MLX Emergency Fallback**
- Model: `mlx-community/Mistral-7B-Instruct-v0.3-4bit` on Mac MPS GPU
- Only loads if HQ offline for 60+ seconds
- Handles simple conversational responses only
- Does NOT execute tools
- RAM cost: ~4.5GB when loaded

---

## Mac Components

### FastAPI Server (`jarvis.py`)
- Port: 8765
- Serves: HUD HTML, WebSocket chat, REST API endpoints
- Launched via: LaunchAgent `com.simonx.simon.plist`
- Log: `~/Projects/AI-Projects/jarvis/jarvis.log`

### WebSocket Handler
- Path: `/ws/{session_id}`
- Message types: `ping`, `stop`, `chat`, `clear`
- Response types: `greeting`, `thinking`, `tool_use`, `chunk`, `done`, `speech_done`, `stats`

### Tools (21 core + plugins)
See `TOOLS_REFERENCE.md` for full list.

### Vision Engine (`vision/simon_vision.py`)
- YOLO26n: object detection, pre-warmed at startup (~150MB)
- Moondream2: scene Q&A, on-demand load only when HQ offline
- DeepFace/ArcFace: face recognition, on-demand load
- Camera: 12MP MacBook built-in, managed by macOS TCC

### Plugin System (`plugin_loader.py`)
- Loads from `plugins/` directory
- Hot-reloads every 3 seconds
- Current plugins: HQ Bridge, Network Tools, Weather

### Knowledge Base (`simon_kb.py`)
- SQLite at `~/.simon-x/simon_kb.db`
- Tables: contacts, messages_cache, memory, session_log, vision tables
- 167 contacts, 20+ memory facts
- Syncs to HQ ChromaDB every 5 minutes

### Security (`simon_security.py`)
- 75 shell command blocklist patterns
- Outbound send scanning (blocks credentials in messages/emails)
- Prompt injection detection
- Trusted contacts: +1XXXXXXXXXX, your@email.com, your@email.com

---

## simon-hq Components

### HQ API (`hq_api_v2_main.py`)
- Port: 8200
- Systemd service: `simon-hq-api`
- Endpoints: `/health`, `/llm/chat`, `/vision/ask`, `/memory/sync`, `/memory/store`, `/memory/search`, `/web/search`, `/web/scrape`, `/ollama/models`, `/startup`

### Ollama
- Port: 11434
- Systemd service: `ollama`
- Default model: llama3.1:8b (kept warm in RAM)
- Vision model: llama3.2-vision:11b

### ChromaDB
- Port: 8100
- Systemd service: `simon-chroma`
- Collection: `simon_memory`
- Receives synced KB facts from Mac every 5 minutes

### PostgreSQL
- Port: 5432
- Database: `simon_brain`
- User: `simon`
- Tables: contacts, memory, messages_cache, session_log, research, sync_state
- Extensions: pgvector, pg_trgm, unaccent, pg_cron
- Status: Installed and configured, SQLite migration PENDING

---

## Network

| Host | Tailscale IP | LAN IP | Role |
|---|---|---|---|
| Mac (M5 MacBook Air) | YOUR_MAC_TAILSCALE_IP | DHCP | Voice, camera, tools |
| simon-hq (Ubuntu i7) | YOUR_HQ_TAILSCALE_IP | YOUR_HQ_LOCAL_IP | LLM, memory, vision |

**Key Ports:**
- `:8765` — SIMON HUD (Mac)
- `:8200` — HQ API (simon-hq)
- `:11434` — Ollama (simon-hq)
- `:8100` — ChromaDB (simon-hq)
- `:5432` — PostgreSQL (simon-hq)

---

## LaunchAgent

Path: `~/Library/LaunchAgents/com.simonx.simon.plist`

Key settings:
- `KeepAlive.Crashed = true` — auto-restart on crash
- `KeepAlive.NetworkState = true` — wait for network before starting
- `ThrottleInterval = 30` — 30-second minimum between restarts
- `TOKENIZERS_PARALLELISM = false` — suppresses HuggingFace warnings
- `PATH = /opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`

---

## Background Tasks (in jarvis.py)

| Task | Interval | Purpose |
|---|---|---|
| `_bg_kb_sync` | 10 min / 6hr | Sync messages, run KB maintenance |
| `_bg_health_check` | 10 min | Run health probes, auto-heal Mail/Messages, escalate alerts after 5 min DOWN |
| `_bg_mlx_prewarm` | 30s poll | Load MLX only if HQ offline 60s+ |
| `_bg_vision_prewarm` | Once at 8s | Pre-warm YOLO26n only |
| `_bg_hq_health` | 30s | Ping HQ, update `_hq_online` flag |
| `_bg_ensure_apps` | Once at 30s | Open Mail and Messages if closed |
| `_bg_android_monitor` | 3 min | Missed call alerts, low battery alerts (requires Android setup) |

## Plugins (v4.4)

| Plugin | File | Tools | Requires |
|---|---|---|---|
| HQ Bridge | `plugins/hq_bridge.py` | 7 | simon-hq + Tailscale |
| Network Tools | `plugins/network_tools.py` | 14 | None (local) |
| Weather | `plugins/weather.py` | 2 | None (free API) |
| Android Bridge | `plugins/android_bridge.py` | 14 | ADB + Android phone on same WiFi |

## Project Structure (v4.4)

```
jarvis/
├── jarvis.py              ← Main server (1,647 lines)
├── config.json            ← All config including android block
├── hud.html               ← Browser HUD
├── simon_kb.py            ← Knowledge base (SQLite)
├── simon_security.py      ← Outbound security gate
├── simon_tool_health.py   ← Health monitor (12 checks)
├── simon_mlx.py           ← MLX emergency fallback
├── simon_db.py            ← PostgreSQL module (pending migration)
├── plugin_loader.py       ← Hot-reload plugin system
├── remote_gpu.py          ← Remote GPU support
├── hq_api_v2_main.py      ← HQ server-side API (runs on simon-hq)
├── hq_reconnect_watchdog.py ← HQ reconnect watchdog
├── start_simon.sh         ← Start script
├── stop_simon.sh          ← Stop script
├── restart_simon.sh       ← Restart script
├── emergency_restart.sh   ← Emergency recovery
├── ANDROID_SETUP.md       ← Android one-time setup guide
├── plugins/
│   ├── hq_bridge.py
│   ├── network_tools.py
│   ├── weather.py
│   └── android_bridge.py  ← NEW v4.4
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CHANGELOG.md
│   ├── KNOWN_ISSUES.md
│   ├── TOOLS_REFERENCE.md
│   ├── OPERATIONS.md
│   ├── NETWORK.md
│   └── TROUBLESHOOTING.md
├── tools/
│   ├── diag_camera.py
│   └── diag_hq.py
├── tests/
│   ├── test_hq_vision.py
│   └── test_simon_hq.py
├── vision/                ← Vision engine (YOLO + Moondream)
├── voices/                ← Piper TTS voice files
├── _archive/              ← Retired files (safe to delete after v4.4 stable)
└── github/                ← Git-tracked clean copy for version control
```
