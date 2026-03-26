# S.I.M.O.N. Plugins

Drop any `.py` file here to add new tools — no editing `jarvis.py` required.

## Included Plugins

| File | Tools | Description |
|---|---|---|
| `weather.py` | `get_weather` | Current conditions + forecast via wttr.in |
| `network_tools.py` | `get_public_ip`, `dns_lookup`, `check_port`, `ip_info` | IT/MSP network utilities |

## How to Write a Plugin

Create `plugins/my_plugin.py` with three things:

```python
# 1. Optional metadata (shows in HUD)
METADATA = {
    "name":        "My Plugin",
    "description": "What it does",
    "version":     "1.0",
}

# 2. Tool definitions — standard Ollama format
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "my_tool_name",
            "description": "Clear description so the LLM knows when to call this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "param": {"type": "string", "description": "What this param does"}
                },
                "required": ["param"]
            }
        }
    }
]

# 3. Async handler — called when LLM selects your tool
async def execute(name: str, args: dict) -> str:
    if name == "my_tool_name":
        param = args.get("param", "")
        # ... your logic ...
        return f"Result: {param}"
    return None  # return None to pass to next handler
```

That's it. SIMON picks it up within 3 seconds — no restart needed.

## Disabling a Plugin

Rename it to start with `_`:
```
_disabled_weather.py   ← SIMON ignores this
```

## Available Context

Plugins can import anything from the SIMON project:

```python
from simon_kb import memory_get, memory_set   # read/write persistent memory
from pathlib import Path
import httpx, asyncio, sqlite3                # all pre-installed
```

## Rules

- `execute()` must be `async`
- Return a `str` — SIMON speaks it directly
- Return `None` to pass the call through to the core dispatcher
- Never crash — wrap your logic in try/except and return an error string
- Tool names must be unique across all plugins and core tools
