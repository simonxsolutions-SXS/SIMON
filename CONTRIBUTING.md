# Contributing to S.I.M.O.N.

Thank you for your interest in contributing. S.I.M.O.N. is an open-source project built to be a privacy-first, fully local AI assistant for macOS.

---

## Before You Start

- Check the [open issues](../../issues) for work that needs doing
- For large changes, open an issue first to discuss before writing code
- All contributions must preserve the core privacy guarantee: **no user data leaves the machine**

---

## Development Setup

```bash
git clone https://github.com/YOUR_USERNAME/simon.git
cd simon

# Install dependencies
pip3.11 install fastapi uvicorn httpx piper-tts --break-system-packages

# Copy config template
cp config.example.json config.json
# Add your Ollama endpoint and key

# Initialize KB
python3.11 simon_kb.py init

# Start in dev mode (logs to console)
python3.11 jarvis.py
```

---

## Project Structure

```
simon/
├── jarvis.py       # Core server — edit tools, LLM logic, TTS here
├── simon_kb.py     # Knowledge base — edit schema, sync, maintenance here
├── hud.html        # Browser HUD — edit UI, voice, state machine here
├── start_simon.sh  # Launch script
└── docs/           # Documentation (markdown)
```

---

## Areas Looking for Contributions

### High Priority

- **Linux port** — replace AppleScript calls with DBus/Flatpak equivalents for GNOME calendar, contacts, etc.
- **Windows port** — Windows Speech API integration, COM automation for Outlook/Calendar
- **Local LLM direct** — llama.cpp Python bindings instead of Ollama API, for zero-network-dependency mode
- **Offline fonts** — bundle Orbitron and Share Tech Mono so the HUD works without Google Fonts

### Medium Priority

- **Plugin system** — allow dropping `.py` files into a `plugins/` folder to add tools without editing `jarvis.py`
- **Additional voice models** — test and document other Piper voices, add a voice selector to the HUD
- **Multi-language support** — wake word and TTS in languages other than English
- **Health check customization** — configurable thresholds, report formats, delivery methods

### Documentation

- Diagrams with actual screenshots (need contributors with the system running)
- Video walkthrough / demo
- Linux/Windows-specific installation guides

---

## Code Style

- **Python:** PEP 8. Type hints on all function signatures. Async for anything that touches the network or subprocess.
- **JavaScript:** ES2020+. No external dependencies beyond what's in the HUD already. Keep it a single file.
- **Comments:** Explain *why*, not *what*. The code shows what — comments explain the reasoning.

---

## Privacy Rules (Non-negotiable)

Any contribution must follow these rules:

1. **No telemetry.** No analytics calls, no crash reporting to external services.
2. **No uploading user data.** Contacts, messages, calendar events, memory — none of it leaves the machine.
3. **Config contains secrets.** `config.json` must never be committed. The `.gitignore` enforces this.
4. **Read-only Apple data.** The `~/Library/Messages/chat.db` and AddressBook DBs are opened read-only via SQLite URI mode. Never write to them.

---

## Submitting a Pull Request

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Test with `python3.11 -m py_compile jarvis.py simon_kb.py` to catch syntax errors
5. Commit with a clear message: `git commit -m "Add: plugin loader for custom tools"`
6. Push and open a PR against `main`

---

## Reporting Issues

Use GitHub Issues. Include:
- macOS version
- Python version (`python3.11 --version`)
- Steps to reproduce
- Relevant log output (`tail -50 jarvis.log`)

Do **not** include your `config.json`, API keys, or personal message content in bug reports.

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
