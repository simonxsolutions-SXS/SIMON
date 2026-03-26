#!/usr/bin/env python3
"""
S.I.M.O.N. Plugin Loader
========================
Drop any .py file into plugins/ to add new tools — no jarvis.py edits needed.

PLUGIN CONTRACT
---------------
Each plugin file must expose:

  TOOLS  (required)
      A list of Ollama-format tool definition dicts, identical to the
      ones in jarvis.py's TOOLS list.  Example:

          TOOLS = [
              {
                  "type": "function",
                  "function": {
                      "name": "my_tool",
                      "description": "Does something useful.",
                      "parameters": {
                          "type": "object",
                          "properties": {
                              "query": {"type": "string", "description": "..."}
                          },
                          "required": ["query"]
                      }
                  }
              }
          ]

  async def execute(name: str, args: dict) -> str | None   (required)
      Called by SIMON whenever one of this plugin's tools is selected.
      - name  : the tool name string (matches TOOLS[*].function.name)
      - args  : parsed arguments dict from the LLM
      Return the result string, or None to fall through to the core dispatcher.

  METADATA  (optional)
      A dict with display info shown in the HUD:

          METADATA = {
              "name":        "My Plugin",       # display name
              "description": "What it does",
              "version":     "1.0",
              "author":      "Your name",
          }

PLUGIN LIFECYCLE
----------------
  - Loaded on SIMON startup from the plugins/ directory
  - Reloaded automatically when a file changes (hot-reload, no restart needed)
  - Each plugin is isolated — a crash in one never affects the others
  - Disabled by naming the file with a leading underscore: _disabled_plugin.py

QUICK EXAMPLE — save as plugins/weather.py:
------------
    METADATA = {"name": "Weather", "version": "1.0"}

    TOOLS = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }
        }
    }]

    async def execute(name, args):
        if name == "get_weather":
            city = args.get("city", "")
            # ... your logic here ...
            return f"Weather in {city}: 72°F, sunny."
"""

import importlib
import importlib.util
import sys
import asyncio
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Callable

PLUGINS_DIR = Path(__file__).parent / "plugins"

# ─── Registry ────────────────────────────────────────────────────────────────
# These are mutated at runtime as plugins load/unload
_plugin_registry: Dict[str, dict] = {}   # plugin_name → {module, tools, metadata, mtime}
_tool_index:      Dict[str, str]  = {}   # tool_name   → plugin_name (for fast dispatch)


# ─── Loader ──────────────────────────────────────────────────────────────────

def _load_plugin(path: Path) -> Optional[dict]:
    """
    Import a single plugin file and validate its contract.
    Returns a plugin entry dict or None on failure.
    """
    name = path.stem  # filename without .py

    spec   = importlib.util.spec_from_file_location(f"plugins.{name}", path)
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"[Plugin] ❌ {name}: import failed — {e}")
        return None

    # Validate required attributes
    if not hasattr(module, "TOOLS"):
        print(f"[Plugin] ⚠️  {name}: missing TOOLS list — skipped")
        return None
    if not hasattr(module, "execute"):
        print(f"[Plugin] ⚠️  {name}: missing execute() function — skipped")
        return None
    if not asyncio.iscoroutinefunction(module.execute):
        print(f"[Plugin] ⚠️  {name}: execute() must be async — skipped")
        return None

    tools    = module.TOOLS
    metadata = getattr(module, "METADATA", {"name": name})
    display  = metadata.get("name", name)
    version  = metadata.get("version", "")
    tool_names = [t["function"]["name"] for t in tools if "function" in t]

    entry = {
        "module":     module,
        "tools":      tools,
        "metadata":   metadata,
        "tool_names": tool_names,
        "mtime":      path.stat().st_mtime,
        "path":       path,
        "error":      None,
    }

    ver_str = f" v{version}" if version else ""
    print(f"[Plugin] ✅ {display}{ver_str} — tools: {', '.join(tool_names)}")
    return entry


def load_all() -> None:
    """
    Scan plugins/ directory and load every valid .py file.
    Skips files starting with '_' (disabled convention).
    Safe to call multiple times — only reloads changed files.
    """
    PLUGINS_DIR.mkdir(exist_ok=True)

    found   = set()
    changed = 0

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue  # disabled
        if path.name == "__init__.py":
            continue

        plugin_name = path.stem
        found.add(plugin_name)
        mtime = path.stat().st_mtime

        # Already loaded and unchanged — skip
        if plugin_name in _plugin_registry:
            if _plugin_registry[plugin_name]["mtime"] == mtime:
                continue
            print(f"[Plugin] 🔄 {plugin_name}: changed, reloading...")

        entry = _load_plugin(path)
        if entry:
            # Remove old tool index entries for this plugin
            _deindex_plugin(plugin_name)
            _plugin_registry[plugin_name] = entry
            # Index all tools this plugin provides
            for tname in entry["tool_names"]:
                _tool_index[tname] = plugin_name
            changed += 1

    # Remove plugins whose files were deleted
    removed = set(_plugin_registry.keys()) - found
    for plugin_name in removed:
        print(f"[Plugin] 🗑️  {plugin_name}: file removed, unloading")
        _deindex_plugin(plugin_name)
        del _plugin_registry[plugin_name]
        changed += 1

    if changed:
        total_tools = sum(len(e["tool_names"]) for e in _plugin_registry.values())
        print(f"[Plugin] {len(_plugin_registry)} plugin(s) loaded | {total_tools} tool(s) registered")


def _deindex_plugin(plugin_name: str) -> None:
    """Remove a plugin's tools from the tool index."""
    if plugin_name not in _plugin_registry:
        return
    for tname in _plugin_registry[plugin_name]["tool_names"]:
        _tool_index.pop(tname, None)


# ─── Dispatch ────────────────────────────────────────────────────────────────

async def dispatch(name: str, args: dict) -> Optional[str]:
    """
    Try to dispatch a tool call to the plugin that owns it.
    Returns the result string, or None if no plugin handles it.
    """
    plugin_name = _tool_index.get(name)
    if not plugin_name:
        return None

    entry = _plugin_registry.get(plugin_name)
    if not entry:
        return None

    try:
        result = await entry["module"].execute(name, args)
        return result  # may be None (plugin declines — falls to core)
    except Exception as e:
        print(f"[Plugin] ❌ {plugin_name}.execute({name}): {e}")
        return f"Plugin error in {plugin_name}: {e}"


# ─── Runtime tool list ───────────────────────────────────────────────────────

def get_plugin_tools() -> List[dict]:
    """Return the merged TOOLS list from all loaded plugins."""
    tools = []
    for entry in _plugin_registry.values():
        tools.extend(entry["tools"])
    return tools


def plugin_status() -> List[dict]:
    """Return status info for every loaded plugin (for HUD / health checks)."""
    result = []
    for pname, entry in _plugin_registry.items():
        result.append({
            "name":       entry["metadata"].get("name", pname),
            "version":    entry["metadata"].get("version", ""),
            "tools":      entry["tool_names"],
            "tool_count": len(entry["tool_names"]),
            "error":      entry["error"],
            "file":       entry["path"].name,
        })
    return result


# ─── Hot-reload watcher ──────────────────────────────────────────────────────

_watcher_thread: Optional[threading.Thread] = None
_watcher_stop   = threading.Event()


def _watch_loop(interval: float = 3.0) -> None:
    """Background thread: poll plugins/ every `interval` seconds for changes."""
    while not _watcher_stop.is_set():
        try:
            load_all()
        except Exception as e:
            print(f"[Plugin] Watcher error: {e}")
        _watcher_stop.wait(interval)


def start_watcher(interval: float = 3.0) -> None:
    """Start the hot-reload background watcher thread."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return
    _watcher_stop.clear()
    _watcher_thread = threading.Thread(
        target=_watch_loop,
        args=(interval,),
        daemon=True,
        name="simon-plugin-watcher",
    )
    _watcher_thread.start()
    print(f"[Plugin] Hot-reload watcher started (polling every {interval}s)")


def stop_watcher() -> None:
    """Stop the hot-reload watcher."""
    _watcher_stop.set()
