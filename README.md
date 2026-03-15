# S.I.M.O.N. — Systems Intelligence & Management Operations Node

<div align="center">

![S.I.M.O.N. HUD](docs/assets/simon_hud_preview.png)

**A fully local, zero-cloud AI assistant for macOS — built on Apple Silicon.**

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue?logo=python)](https://www.python.org)
[![macOS](https://img.shields.io/badge/macOS-26%2B-black?logo=apple)](https://www.apple.com/macos)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open%20Source-%E2%9D%A4-red)](https://github.com)

</div>

---

## What is S.I.M.O.N.?

S.I.M.O.N. is a **self-hosted, privacy-first AI personal assistant** that runs entirely on your Mac. No subscriptions. No data sent to third parties. No always-on microphone phoning home.

It gives you a JARVIS-style voice interface connected to your real digital life — calendar, messages, email, contacts, reminders — backed by a local SQLite knowledge base that learns about you over time.

**Key principles:**
- 🔒 **100% local** — all data stays on your machine
- 🎙️ **Voice-first** — wake word activation, continuous conversation mode
- 🧠 **Persistent memory** — remembers facts across restarts
- ⚡ **Zero-cost** — uses open-weight models via Ollama (local or cloud)
- 🔧 **Self-healing** — automatic deduplication, cache expiry, DB maintenance

---

## Feature Overview

| Feature | Details |
|---|---|
| **Voice Interface** | Wake word (`Simon`), continuous conversation, kill phrases |
| **AI Model** | Mistral Large or any Ollama-compatible model |
| **Text-to-Speech** | Piper TTS (local, no API) — British Alan voice |
| **HUD** | Real-time neural-network brain canvas, system vitals, activity log |
| **Calendar** | Read/create events via Calendar.app |
| **Messages** | Read/send iMessage and SMS via Messages.app SQLite |
| **Email** | Read unread mail across multiple accounts via Mail.app |
| **Reminders** | Read/create via Reminders.app |
| **Contacts** | Full contact search and phone resolution |
| **Knowledge Base** | Local SQLite — contacts cache, message cache (48h TTL), permanent memory |
| **Health Checks** | 3x daily system reports via iMessage |
| **Auto-start** | launchd plist — starts on login, restarts on crash |
| **Self-healing DB** | Dedup, TTL expiry, vacuum every 6 hours |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     macOS (Apple Silicon)                    │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              S.I.M.O.N. Core  (jarvis.py)            │  │
│  │                                                      │  │
│  │  ┌─────────────┐   ┌──────────┐   ┌─────────────┐  │  │
│  │  │  FastAPI    │   │  Ollama  │   │  Piper TTS  │  │  │
│  │  │  WebSocket  │◄──│  (LLM)   │   │  (voice)    │  │  │
│  │  │  Server     │   │          │   │             │  │  │
│  │  └──────┬──────┘   └──────────┘   └─────────────┘  │  │
│  │         │                                           │  │
│  │  ┌──────▼──────────────────────────────────────┐   │  │
│  │  │              Tool Dispatcher                 │   │  │
│  │  │  Calendar │ Messages │ Email │ Reminders    │   │  │
│  │  │  Contacts │ Shell    │ KB    │ System       │   │  │
│  │  └──────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────┘  │
│                           │                                 │
│  ┌────────────────────────▼───────────────────────────┐    │
│  │          Local Knowledge Base  (simon_kb.py)        │    │
│  │  ~/.simon-x/simon_kb.db                            │    │
│  │                                                     │    │
│  │  contacts (167 people, 1 row/person, no dups)      │    │
│  │  messages_cache (48h TTL, auto-expires)            │    │
│  │  memory (permanent facts, survives restarts)       │    │
│  │  email_senders (importance-ranked)                 │    │
│  │  session_log (30-day rolling window)               │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Chrome HUD │  │  Apple Apps  │  │  launchd         │  │
│  │  hud.html   │  │  via Apple-  │  │  Auto-start      │  │
│  │  WebSocket  │  │  Script      │  │  Crash recovery  │  │
│  └─────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| macOS | 13+ (Sonoma/Sequoia recommended) | Apple Silicon preferred |
| Python | 3.11 | `/opt/homebrew/bin/python3.11` |
| Homebrew | Any | [brew.sh](https://brew.sh) |
| Google Chrome | Any | Required for voice (Web Speech API) |
| Ollama | Latest | [ollama.ai](https://ollama.ai) or Ollama Cloud |

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/simon.git
cd simon

# 2. Install Python dependencies
pip3.11 install fastapi uvicorn httpx piper-tts --break-system-packages

# 3. Download the Piper voice model
mkdir -p voices
curl -L -o voices/en_GB-alan-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx
curl -L -o voices/en_GB-alan-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json

# 4. Configure
cp config.example.json config.json
# Edit config.json with your Ollama endpoint and model

# 5. Initialize the knowledge base
python3.11 simon_kb.py init
python3.11 simon_kb.py sync --force

# 6. Launch
simon                      # if alias is set up (see docs)
# or
bash start_simon.sh
```

### Terminal Aliases

Add to `~/.zshrc`:

```bash
alias simon='bash /path/to/simon/start_simon.sh'
alias simonlive='bash /path/to/simon/start_simon.sh'
alias simonlog='tail -f /path/to/simon/jarvis.log'
alias simonstop='lsof -ti tcp:8765 | xargs kill -9 2>/dev/null'
alias simonrestart='simonstop; sleep 2; simon'
```

---

## Voice Commands

| Say | What happens |
|---|---|
| `Simon` | Wake word — activates listening |
| `Simon, calendar today` | Read today's events |
| `Simon, check my messages` | Scan all conversations (last 24h) |
| `Simon, check my email` | Read unread emails |
| `Simon, text [name] [message]` | Send iMessage/SMS |
| `Simon, system check` | CPU, RAM, disk report |
| `Simon, set a reminder` | Create Reminders.app entry |
| `Simon, remember that [fact]` | Store permanently in KB |
| `Simon, what do you remember about [topic]` | Search KB memory |
| `Simon, upcoming events` | Next 7 days from calendar |
| Kill phrase (e.g. `stand by`) | Exit conversation mode |

---

## Documentation

| Document | Description |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | System design, data flow, component breakdown |
| [Installation Guide](docs/INSTALLATION.md) | Full setup with troubleshooting |
| [Configuration Reference](docs/CONFIGURATION.md) | All config options explained |
| [Knowledge Base](docs/KNOWLEDGE_BASE.md) | KB schema, sync, memory, maintenance |
| [HUD Reference](docs/HUD.md) | Browser HUD — states, controls, activity log |
| [Voice & Speech](docs/VOICE.md) | Wake word, SR behavior, TTS, conversation mode |
| [Health Checks](docs/HEALTH_CHECKS.md) | Automated 3x daily reports via iMessage |
| [API Reference](docs/API.md) | WebSocket protocol and REST endpoints |
| [Tools Reference](docs/TOOLS.md) | All 14 AI tools — inputs, outputs, examples |
| [Contributing](CONTRIBUTING.md) | How to contribute |

---

## Project Structure

```
simon/
├── jarvis.py              # Core server — FastAPI, LLM, tools, TTS, WebSocket
├── simon_kb.py            # Local knowledge base — contacts, memory, messages cache
├── hud.html               # Browser HUD — neural brain canvas, voice, chat
├── start_simon.sh         # Launch script with TTS test and Chrome open
├── config.example.json    # Configuration template
├── voices/                # Piper TTS voice models
│   ├── en_GB-alan-medium.onnx
│   └── en_GB-alan-medium.onnx.json
├── ~/.simon-x/            # Runtime data directory (outside repo)
│   ├── simon_kb.db        # SQLite knowledge base
│   ├── simon_health_check.py
│   ├── simon_catchup.py
│   ├── mcp_guard.sh
│   └── reports/           # Health check JSON reports
└── docs/                  # Full documentation
    ├── ARCHITECTURE.md
    ├── INSTALLATION.md
    ├── CONFIGURATION.md
    ├── KNOWLEDGE_BASE.md
    ├── HUD.md
    ├── VOICE.md
    ├── HEALTH_CHECKS.md
    ├── API.md
    └── TOOLS.md
```

---

## Privacy & Security

S.I.M.O.N. is designed with privacy as a hard constraint — not an afterthought.

- **No telemetry** — zero analytics, zero usage reporting
- **Local SQLite only** — `~/.simon-x/simon_kb.db` never leaves your machine
- **Contacts read directly** from AddressBook SQLite — no AppleScript, no API calls
- **Messages read directly** from `~/Library/Messages/chat.db` — WAL-mode read-only
- **LLM requests** go only to your configured Ollama endpoint (local or self-hosted cloud)
- **TTS is offline** — Piper runs entirely on-device
- **No always-on mic** — Chrome's Web Speech API only activates in the focused tab

The only external network calls are:
1. LLM inference to your Ollama endpoint
2. Font loading from Google Fonts (HUD only — can be made offline, see docs)

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Areas actively looking for help:
- Linux / Windows port
- Local LLM integration (llama.cpp direct)
- Additional voice models
- Plugin/tool system
- Offline font bundle

---

## License

MIT — see [LICENSE](LICENSE)

---

## Acknowledgements

- [Ollama](https://ollama.ai) — local LLM runtime
- [Piper TTS](https://github.com/rhasspy/piper) — offline neural text-to-speech
- [FastAPI](https://fastapi.tiangolo.com) — async Python web framework
- [Mistral AI](https://mistral.ai) — Mistral Large model
