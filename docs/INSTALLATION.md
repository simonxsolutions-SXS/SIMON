# Installation Guide — S.I.M.O.N.

> Full setup from zero to talking AI assistant. Estimated time: 20–30 minutes.

---

## Prerequisites

### Required Hardware
- **Mac with Apple Silicon** (M1 / M2 / M3 / M4 / M5 or later)
- Minimum 16GB RAM recommended (8GB will work with smaller models)
- macOS 13 Ventura or later

### Required Software

| Software | Version | Install |
|---|---|---|
| Homebrew | Any | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| Python 3.11 | 3.11.x | `brew install python@3.11` |
| Google Chrome | Any | [google.com/chrome](https://www.google.com/chrome) |
| Ollama | Latest | `brew install ollama` or [ollama.ai](https://ollama.ai) |

---

## Step 1: System Permissions

S.I.M.O.N. reads your Messages and Contacts databases directly. You must grant Terminal Full Disk Access before setup.

1. Open **System Settings** → **Privacy & Security** → **Full Disk Access**
2. Click the `+` button
3. Navigate to `/Applications/Utilities/Terminal.app` and add it
4. Toggle it **ON**
5. Restart Terminal

> **Why?** S.I.M.O.N. reads `~/Library/Messages/chat.db` directly (WAL mode, read-only). Without Full Disk Access, this returns a permission error.

---

## Step 2: Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/simon.git
cd simon
```

---

## Step 3: Install Python Dependencies

```bash
pip3.11 install fastapi uvicorn httpx piper-tts --break-system-packages
```

Verify:
```bash
python3.11 -c "import fastapi, uvicorn, httpx, piper; print('✅ All dependencies OK')"
```

---

## Step 4: Download a Voice Model

S.I.M.O.N. uses [Piper TTS](https://github.com/rhasspy/piper) for offline voice synthesis.

```bash
mkdir -p voices

# British Alan (default, recommended)
curl -L -o voices/en_GB-alan-medium.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx"

curl -L -o voices/en_GB-alan-medium.onnx.json \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json"
```

Other voices are available at [HuggingFace Piper Voices](https://huggingface.co/rhasspy/piper-voices/tree/main). Update `PIPER_MODEL` in `jarvis.py` to switch.

---

## Step 5: Configure Ollama

### Option A: Local Ollama (fully offline)

```bash
# Install and start Ollama
brew install ollama
ollama serve &

# Pull a model (choose based on your RAM)
ollama pull mistral          # 7B — 8GB+ RAM
ollama pull mistral-large    # 123B — requires high-end hardware or Ollama Cloud
ollama pull llama3.2         # 3B — great for low-RAM machines
```

### Option B: Ollama Cloud (remote inference)

Sign up at [ollama.com](https://ollama.com) and get an API key.

### Configure `config.json`

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "ollama_cloud_url": "https://api.ollama.com",
  "ollama_cloud_key": "YOUR_API_KEY_HERE",
  "model": "mistral-large",
  "port": 8765
}
```

For local Ollama:
```json
{
  "ollama_cloud_url": "http://localhost:11434",
  "ollama_cloud_key": "ollama",
  "model": "mistral",
  "port": 8765
}
```

> ⚠️ **Never commit `config.json`** — it contains your API key. It is listed in `.gitignore`.

---

## Step 6: Initialize the Knowledge Base

```bash
python3.11 simon_kb.py init
python3.11 simon_kb.py sync --force
```

Expected output:
```
✅ KB initialized — /Users/YOU/.simon-x/simon_kb.db
  contacts     167 upserted
  messages     12 new
  167 contacts | 12 messages | 0 memory | 112.0 KB
```

### Seed Your Memory (optional but recommended)

```bash
# Store facts SIMON should always know
python3.11 simon_kb.py memory set "your_name"     "Your Name"            person
python3.11 simon_kb.py memory set "company"        "Your Company Name"    person
python3.11 simon_kb.py memory set "city"           "Your City"            person
```

---

## Step 7: Launch S.I.M.O.N.

```bash
bash start_simon.sh
```

This will:
1. Kill any process on port 8765
2. Run a TTS test (you'll hear a voice)
3. Start the server
4. Open Chrome to `http://localhost:8765`

---

## Step 8: Set Up Terminal Aliases

```bash
echo '# S.I.M.O.N. aliases' >> ~/.zshrc
echo 'alias simon="bash /path/to/simon/start_simon.sh"' >> ~/.zshrc
echo 'alias simonlog="tail -f /path/to/simon/jarvis.log"' >> ~/.zshrc
echo 'alias simonstop="lsof -ti tcp:8765 | xargs kill -9 2>/dev/null"' >> ~/.zshrc
echo 'alias simonrestart="simonstop; sleep 2; simon"' >> ~/.zshrc
source ~/.zshrc
```

Now just type `simon` to start.

---

## Step 9: Auto-Start with launchd (optional)

To have S.I.M.O.N. start automatically on login:

```bash
# Copy and edit the plist template
cp launchd/com.simon.plist ~/Library/LaunchAgents/

# Edit to set your absolute paths
nano ~/Library/LaunchAgents/com.simon.plist

# Load it
launchctl load ~/Library/LaunchAgents/com.simon.plist
```

---

## Step 10: Allow Microphone Access

1. Open Chrome and navigate to `http://localhost:8765`
2. Chrome will prompt for microphone permission — click **Allow**
3. Say `Simon` — the brain should light up green and you'll hear a response

> **Tip:** Keep the S.I.M.O.N. tab active (not in the background) for voice to work. Chrome's Web Speech API pauses when the tab loses focus.

---

## Troubleshooting

### Voice not working

**Symptom:** Activity log shows `SR: aborted` repeatedly

**Cause:** Chrome pauses speech recognition when the tab is not focused.

**Fix:** Click the S.I.M.O.N. tab to bring it to the foreground. SR will resume automatically within 400ms.

---

### "Cannot read messages: Full Disk Access required"

**Fix:** See Step 1. Grant Full Disk Access to Terminal in System Settings.

---

### Server won't start — port in use

```bash
lsof -ti tcp:8765 | xargs kill -9
```

---

### TTS not working

```bash
python3.11 -c "
from piper.voice import PiperVoice
import wave
v = PiperVoice.load('voices/en_GB-alan-medium.onnx')
with wave.open('/tmp/test.wav','wb') as w:
    v.synthesize_wav('Hello.', w)
print('TTS OK')
"
afplay /tmp/test.wav
```

If this fails, re-run:
```bash
pip3.11 install piper-tts --break-system-packages --force-reinstall
```

---

### Contacts not syncing

```bash
python3.11 simon_kb.py sync --force
python3.11 simon_kb.py contacts
```

If 0 contacts are returned, check that AddressBook source DBs exist:
```bash
find ~/Library/Application\ Support/AddressBook/Sources -name "*.abcddb" | head -5
```

---

### Model not responding

Check Ollama is running:
```bash
curl http://localhost:11434/api/tags   # local
# or
curl YOUR_CLOUD_URL/api/tags -H "Authorization: Bearer YOUR_KEY"
```

---

## Verifying Your Installation

```bash
# 1. Server health
curl -s http://localhost:8765/api/status | python3.11 -m json.tool

# 2. KB status
python3.11 simon_kb.py status

# 3. Message access
python3.11 simon_kb.py messages 24

# 4. Contact resolution
python3.11 simon_kb.py contacts
```

Expected API status response:
```json
{
  "time": "10:30:00",
  "date": "Saturday, March 14, 2026",
  "cpu": 18.5,
  "mem_gb": 14.2,
  "mem_max": 24,
  "disk_used": "12G",
  "disk_avail": "911G",
  "disk_pct": 2,
  "ip": "10.0.0.1",
  "load": "2.1 / 2.3 / 2.4"
}
```
