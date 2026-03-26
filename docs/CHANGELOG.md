# S.I.M.O.N. Changelog
**Simon-X Solutions | [OWNER_NAME]**

---

## v4.5 — March 21, 2026

**Theme:** Boot automation, HQ intelligence injection, LM Studio integration, self-repair engine.

### New Features
- **Auto-boot on login** — `com.simonx.simon` LaunchAgent updated: `RunAtLoad=true`, crash-only `KeepAlive`, silent boot mode (no browser popup, no prompts). SIMON starts automatically when you log into your Mac.
- **10-second startup delay before greeting** — `_STARTUP_READY` flag ensures SIMON never greets mid-boot. Waits 10s after server start for health checks, HQ handshake, and plugins to settle. HUD shows "Initializing..." until ready.
- **HQ Brain injection** (`simon_brain.md`) — HQ API v2.2 loads an 83-line SIMON context file at startup and injects it as the system prompt on every chat request. HQ now knows your devices, contacts, architecture, and all repair procedures without being told.
- **`/reload_brain` endpoint** — Update `simon_brain.md` on simon-hq and call `POST /reload_brain` to refresh without restarting the service.
- **LM Studio plugin** (`plugins/lm_studio.py`) — 4 tools connecting SIMON to GPU-accelerated Mistral 7B Instruct on simon-hq:
  - `lm_status` — check server status and loaded model
  - `lm_ask` — query the LM Studio model directly
  - `lm_list_models` — list available models
  - `lm_compare` — fire same prompt at HQ Ollama and LM Studio simultaneously, compare responses side-by-side
- **Self-repair engine** (`simon_healer.py`) — standalone repair tool with 7 auto-fix classes:
  - `FixMailApp`, `FixMessagesApp` — auto-reopen closed apps
  - `FixPortConflict` — kill processes holding port 8765
  - `FixKBIntegrity` — VACUUM + integrity_check on SQLite KB
  - `FixPiperTTS` — detect missing Piper binary, provide reinstall instructions
  - `FixStaleLog` — trim jarvis.log to 10k lines when oversized
  - `FixADBReconnect` — reconnect ADB via WiFi then Tailscale failover
- **`repair_simon` voice tool** — "Simon, repair yourself" / "Simon, run self-diagnostics" triggers the healer engine
- **SSH key access to simon-hq** — passwordless SSH from Mac to simon-hq established. Enables automated remote management.
- **SMS sent-box fix** — `android_send_sms` now writes a record to `content://sms/sent` after dispatch so messages appear in Google Messages app on Pixel 9a.

### Improvements
- `start_simon.sh` rewritten: boot mode (silent), log rotation at 20MB, cleaner output, removed legacy `hq_reconnect_watchdog` launch (redundant since hq_bridge v2.1)
- HQ API v2.2: FastAPI deprecation fixed (lifespan handler), brain injection, `/reload_brain` endpoint
- `config.json`: added `lm_studio_url` key
- HQ services (`simon-hq-api`, `ollama`, `simon-chroma`) confirmed enabled on boot via systemd

### Documentation
- `docs/HQ_REPAIR_RUNBOOK.md` — comprehensive repair reference for both humans and the HQ LLM model. Covers 21 known issues with exact commands.
- `docs/USER_REPAIR_GUIDE.md` — step-by-step repair guide written for the owner, not engineers.
- `docs/IMPROVEMENTS.md` — research findings, recommended upgrades, mobile access plan, weakness analysis.

---

## v4.4 — March 21, 2026

**Theme:** Android integration, code quality, and proactive intelligence.

### New Features

- **Android Bridge plugin** (`plugins/android_bridge.py`) — 14 voice-callable tools for full Android device control via ADB over WiFi
  - `android_connect` — connect/reconnect to phone
  - `android_status` — battery, WiFi, signal, ADB state
  - `android_read_sms` — read inbox with optional filtering
  - `android_send_sms` — send texts (with security confirmation)
  - `android_notifications` — live notification feed from phone
  - `android_call_log` — call history with type filtering (missed/incoming/outgoing)
  - `android_contacts_search` — search phone contacts by name or number
  - `android_make_call` — dial numbers by voice
  - `android_end_call` — hang up current call
  - `android_open_app` — launch any app by name (50+ common apps pre-mapped)
  - `android_screenshot` — capture phone screen to file
  - `android_get_location` — GPS coordinates from phone
  - `android_list_apps` — list installed apps with optional filter
  - `android_device_info` — model, Android version, carrier, storage
- **Calendar conflict detection** — `tool_create_calendar_event` now checks for overlapping events before creating and warns [OWNER] with conflicting event names/times
- **Health escalation alerts** — health monitor now fires a macOS Notification Center alert when any tool stays DOWN for 5+ continuous minutes (MLX and Vision intentionally excluded)
- **Android proactive monitor** (`_bg_android_monitor`) — background task checks every 3 minutes for:
  - New missed calls → macOS notification
  - Battery ≤ 20% → low battery alert
  - Battery ≤ 10% → critical battery alert
- **`ANDROID_SETUP.md`** — step-by-step one-time setup guide for ADB WiFi pairing

### Bug Fixes
- Fixed: FastAPI `DeprecationWarning: on_event is deprecated` — migrated to `lifespan` context manager
- Fixed: HQ Bridge circular import warning on every startup — deferred registration via `_deferred_init()`
- Fixed: Duplicate `import os` in jarvis.py
- Added: `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` env var to suppress macOS fork safety log noise

### Code Quality
- Removed duplicate `import os` from jarvis.py
- `asynccontextmanager` added for clean lifespan management
- Health monitor now tracks sustained DOWN duration per-tool with automatic reset on recovery

### Project Cleanup
- 37 dead files moved to `_archive/` (backup snapshots, one-time fix/patch scripts, old setup scripts)
- `tools/` directory created for diagnostic scripts (`diag_camera.py`, `diag_hq.py`)
- `tests/` directory created for test scripts (`test_hq_vision.py`, `test_simon_hq.py`)
- `_archive/README.md` documents what was archived and why

### Config Changes
- `config.json` — new `"android"` block with `adb_host`, `adb_port`, `device_name`, `enabled`

### Files Changed
- `jarvis.py` — lifespan migration, conflict detection, health escalation, android monitor
- `plugins/hq_bridge.py` — deferred init to fix circular import
- `plugins/android_bridge.py` — NEW (14 tools)
- `config.json` — added android config block
- `ANDROID_SETUP.md` — NEW setup guide
- `_archive/` — NEW (37 archived files)
- `tools/` — NEW directory
- `tests/` — NEW directory
- `docs/` — updated CHANGELOG, KNOWN_ISSUES, TOOLS_REFERENCE, ARCHITECTURE

---

## v4.3 — March 21, 2026

**Theme:** Stability, reliability, and RAM efficiency.

### Breaking Changes
- HQ no longer receives tool definitions. If you were relying on HQ to call tools directly, that path is gone. All tool calls now go exclusively to Mistral Large via Cloud.
- `hq_bridge.py` startup behavior changed — it no longer blocks. Scripts that assumed HQ was ready immediately after import will need adjustment.

### New Features
- **Three-tier LLM routing** — HQ for conversation, Cloud for tools, MLX for emergency fallback
- **start_simon.sh** — syntax-checked startup script that opens Chrome automatically
- **stop_simon.sh** — clean shutdown with confirmation
- **restart_simon.sh** — stop + start in one command
- **Auto-heal** — Mail.app and Messages.app auto-reopen when health check detects them as DOWN
- **Tool argument sanitizer** — strips malformed wrapper keys from LLM tool call responses

### Bug Fixes
- Fixed: SIMON listening but never responding (HQ misrouting all requests to hq_ask)
- Fixed: Startup hanging for up to 5 minutes while HQ Bridge retried
- Fixed: `SyntaxError` in `tool_vision_detect` — f-string backslash incompatible with Python 3.11
- Fixed: `ifconfig: No such file or directory` — added `/usr/sbin:/sbin` to PATH
- Fixed: Tool calls crashing with `TypeError: unexpected keyword argument` for malformed LLM args

### Performance
- RAM at idle: ~17GB → ~10–11GB (6–7GB saved)
- MLX Mistral 7B (4.5GB) no longer pre-loaded at startup
- Moondream2 (3.5GB) no longer pre-loaded at startup
- SIMON greets in <3 seconds regardless of HQ status (was up to 5 minutes)

### Files Changed
- `jarvis.py` — full rewrite of LLM routing, RAM optimization, all bug fixes
- `plugins/hq_bridge.py` — v2.0 → v2.1 non-blocking handshake
- `Library/LaunchAgents/com.simonx.simon.plist` — PATH update
- `start_simon.sh` — NEW
- `stop_simon.sh` — NEW
- `restart_simon.sh` — NEW
- `docs/` — NEW (this documentation folder)

---

## v4.2 — (Prior Session)

**Theme:** HQ integration, vision pipeline, health monitoring.

### New Features
- simon-hq Ubuntu server brought online with Ollama + ChromaDB
- HQ Bridge plugin (`plugins/hq_bridge.py`) — Mac ↔ HQ communication
- `llama3.2-vision:11b` installed on HQ for camera Q&A
- Vision engine (`vision/simon_vision.py`) — YOLO26n + Moondream2 + DeepFace
- Health monitor (`simon_tool_health.py`) — 12 tool checks every 10 minutes
- Context summarization — long conversations compressed automatically
- Session logging in SQLite KB
- `TOKENIZERS_PARALLELISM=false` added to suppress HuggingFace warnings

### Architecture
- Single-tier routing: all requests went to HQ (with tools) or Cloud fallback
- MLX Mistral 7B pre-warmed at startup (later identified as RAM issue)
- Moondream2 pre-warmed at startup (later identified as RAM issue)

### Known Issues at End of v4.2
- HQ misrouting all tool requests to `hq_ask` (fixed in v4.3)
- HQ Bridge blocking startup (fixed in v4.3)
- RAM at ~17GB idle (fixed in v4.3)

---

## v4.1 — (Earlier Session)

**Theme:** Security hardening and plugin system.

### New Features
- `simon_security.py` — outbound send scanning, shell blocklist (75 patterns), injection detection
- Plugin loader with hot-reload (`plugin_loader.py`)
- Network tools plugin (`plugins/network_tools.py`) — 14 network diagnostic tools
- Weather plugin (`plugins/weather.py`)
- Knowledge base (`simon_kb.py`) — SQLite contacts, messages cache, memory, session log
- `memory_set` / `memory_search` tools — persistent facts across sessions
- Trusted contacts list in config

### Architecture
- FastAPI on :8765 with WebSocket chat
- Piper TTS (Alan British voice) for speech output
- Web Speech API for voice input
- HUD (`hud.html`) with system stats panel

---

## v4.0 — (Earlier Session)

**Theme:** V4 foundation — FastAPI, WebSocket, HUD.

### New Features
- Complete rewrite from previous conversation-only interface to full agent framework
- FastAPI server replacing Flask
- WebSocket real-time streaming of responses
- HUD browser interface with live stats
- Tool calling architecture — 21 core macOS tools
- LaunchAgent for auto-start on login
- Context window management with summarization

---

## v3.x and Earlier

Pre-HUD versions running as terminal scripts. Voice input/output via macOS `say` and `speech_recognition`. Single-model architecture with no HQ. Not documented here — refer to earlier backup files (`jarvis.py.pre_vision_patch`, `jarvis.py.pre_issue_fix`, `jarvis.py.backup`).
