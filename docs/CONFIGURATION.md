# Configuration Reference — S.I.M.O.N.

> Every configuration option explained.

---

## config.json

The main configuration file. **Never commit this file** — it contains your API key.

```json
{
  "ollama_cloud_url": "https://api.ollama.com",
  "ollama_cloud_key": "your-api-key-here",
  "model": "mistral-large",
  "port": 8765
}
```

| Field | Type | Description |
|---|---|---|
| `ollama_cloud_url` | string | Ollama API base URL. Use `http://localhost:11434` for local. |
| `ollama_cloud_key` | string | API key. Use `"ollama"` for local instances. |
| `model` | string | Model name. Any Ollama-compatible model. |
| `port` | integer | Port the server listens on. Default `8765`. |

### Recommended Models by Hardware

| Model | RAM Required | Speed | Quality |
|---|---|---|---|
| `llama3.2:3b` | 8GB+ | Fast | Good for simple tasks |
| `mistral:7b` | 8GB+ | Fast | Excellent balance |
| `mistral-large` | Cloud/High-end | Medium | Best quality |
| `llama3.1:70b` | 48GB+ | Slow | High quality locally |

---

## jarvis.py — Runtime Constants

These are set at the top of `jarvis.py` and can be changed for your setup:

```python
SUMM_MODEL  = "gemma3:12b"    # Context compression model (fast, local)
PIPER_MODEL = "voices/en_GB-alan-medium.onnx"  # TTS voice
HUD_PORT    = cfg["port"]     # From config.json
```

### Context Window Management

```python
SUMM_TRIGGER = 24   # Compress history when conversation reaches 24 messages
KEEP_RECENT  = 16   # Keep last 16 turns verbatim after compression
```

Adjust based on your model's context window:
- Small models (7B): reduce `SUMM_TRIGGER` to 16, `KEEP_RECENT` to 10
- Large models (70B+): increase `SUMM_TRIGGER` to 40, `KEEP_RECENT` to 24

---

## simon_kb.py — KB Constants

```python
KB_PATH              = Path.home() / ".simon-x" / "simon_kb.db"
MESSAGES_DB          = Path.home() / "Library" / "Messages" / "chat.db"
AB_SOURCES           = Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"
MSG_CACHE_TTL_HOURS  = 48   # How long message cache rows survive
```

### Adjusting Message Cache TTL

Default is 48 hours. If you want messages to expire faster:

```python
MSG_CACHE_TTL_HOURS = 24   # 24 hours
```

If you want to keep messages longer (not recommended — messages are not intended as permanent storage):

```python
MSG_CACHE_TTL_HOURS = 72   # 3 days
```

---

## hud.html — HUD Constants

Find these at the top of the `<script>` section:

```js
const WS_URL      = `ws://${location.host}/ws/${Date.now()}`;
let wakeThresh    = 0.55;    // Wake word confidence threshold (0.0 – 1.0)
const _MAX_LOG    = 200;     // Max activity log entries before pruning
```

### Wake Sensitivity

The wake threshold can also be adjusted live in the HUD using the slider in the left panel. Range: 0.0 (very sensitive) to 1.0 (very strict).

**Recommended values:**
- `0.45` — noisy environment, you want it to respond easily
- `0.55` — balanced (default)
- `0.70` — quiet environment, want to avoid accidental triggers

### Kill Phrases

Edit the array in `hud.html` to customize which phrases exit conversation mode:

```js
const KILL_PHRASES = [
  'give me a second',
  'give me a moment',
  "i'll be right back",
  'stand by',
  'standby',
  "that's all",
  'thats all',
  'go to sleep',
  'stop listening',
  'goodbye simon',
  'goodnight simon'
];
```

---

## launchd Plists

### com.simon.plist (auto-start + crash recovery)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.simon</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3.11</string>
        <string>/path/to/simon/jarvis.py</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>/path/to/simon</string>
    
    <!-- Start on login -->
    <key>RunAtLoad</key>
    <true/>
    
    <!-- Restart if crashed -->
    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
    </dict>
    
    <!-- Log output -->
    <key>StandardOutPath</key>
    <string>/path/to/simon/jarvis.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/simon/jarvis.log</string>
</dict>
</plist>
```

### Health Check Plists (scheduled reports)

```xml
<!-- Morning check at 7:45 AM -->
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>45</integer>
</dict>
```

| Plist | Schedule |
|---|---|
| `com.simon.healthcheck.morning` | 7:45 AM daily |
| `com.simon.healthcheck.afternoon` | 3:00 PM daily |
| `com.simon.healthcheck.evening` | 9:00 PM daily |
| `com.simon.healthcheck.catchup` | Every login (catches missed checks) |

---

## Environment Variables

S.I.M.O.N. does not use environment variables for configuration. All settings are in `config.json`. This avoids shell session dependencies when running under launchd.

---

## .gitignore

The repository includes a `.gitignore` that excludes:

```
config.json          # Contains API key
*.log                # Log files
__pycache__/         # Python cache
*.pyc                # Compiled Python
voices/*.onnx        # Large binary files (download separately)
voices/*.onnx.json   # Voice config (download separately)
~/.simon-x/          # Runtime data directory
```

When publishing your fork, ensure `config.json` is never committed. Use `config.example.json` as the template.
