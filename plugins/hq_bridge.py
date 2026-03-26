"""
S.I.M.O.N. HQ Bridge Plugin — hq_bridge.py (v2.1 — Non-blocking)
=================================================================
Simon-X Solutions | [OWNER_NAME]

CRITICAL FIX v2.1:
  The startup handshake previously blocked for up to 5 MINUTES trying
  to reach HQ. During that window SIMON could not respond to anything.

  Fix: handshake runs in a true background task with a 4s timeout per
  attempt. Cloud fallback is instant. SIMON greets and responds normally
  within 3 seconds of boot regardless of HQ status.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

_cfg_path = Path(__file__).parent.parent / "config.json"
try:
    _cfg = json.loads(_cfg_path.read_text())
except Exception:
    _cfg = {}

HQ_URL   = _cfg.get("hq_api_url",  "http://YOUR_HQ_TAILSCALE_IP:8200").rstrip("/")
HQ_KEY   = _cfg.get("hq_api_key",  "")
HQ_MODEL = _cfg.get("hq_model",    "qwen2.5:7b")
MAC_PORT = _cfg.get("port",        8765)
MAC_TS_URL = f"http://YOUR_MAC_TAILSCALE_IP:{MAC_PORT}"

TIMEOUT_CHAT   = 90
TIMEOUT_FAST   = 10
TIMEOUT_SCRAPE = 45

_hq_available    = False
_hq_last_check   = 0.0
_hq_check_interval = 30
_sync_thread: Optional[threading.Thread] = None
_sync_stop = threading.Event()

METADATA = {
    "name":        "HQ Bridge",
    "description": "Unified intelligence — Mac voice + HQ muscle. LLM routing, memory sync, web tools.",
    "version":     "2.1",
    "author":      "Simon-X Solutions",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "hq_status",
            "description": "Check simon-hq status: Ollama, ChromaDB, CPU, RAM, uptime, models. Use when asked 'HQ status', 'is HQ online', 'check HQ', 'system status'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hq_ask",
            "description": "Send a prompt to the local Ollama LLM on simon-hq for deep analysis or heavy reasoning. Use when asked 'ask HQ', 'deep analysis', 'analyze', 'use HQ to think about'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Full prompt to send to HQ Ollama"},
                    "model":  {"type": "string", "description": f"Model override (default: {HQ_MODEL})"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hq_web_search",
            "description": "Search the web via simon-hq using DuckDuckGo. Use when asked to 'search the web', 'look up', 'find information about', 'google', 'search for'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Results to return (default 5)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hq_scrape",
            "description": "Scrape a URL via simon-hq and return clean text. Use when asked to 'scrape', 'read the page at', 'get content from URL', 'what does this website say'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":     {"type": "string", "description": "Full URL starting with http"},
                    "extract": {"type": "string", "description": "'text' (default), 'links', or 'full'"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hq_memory_store",
            "description": "Store a document in ChromaDB on simon-hq for semantic search later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document":   {"type": "string", "description": "Text to store"},
                    "collection": {"type": "string", "description": "Collection name (default: simon_memory)"},
                    "metadata":   {"type": "object", "description": "Optional metadata dict"}
                },
                "required": ["document"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hq_memory_search",
            "description": "Semantically search ChromaDB on simon-hq. Use when asked 'search HQ memory', 'find in vector database', 'what do we have on', 'do we know anything about'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":      {"type": "string", "description": "Natural language search query"},
                    "collection": {"type": "string", "description": "Collection to search (default: simon_memory)"},
                    "n_results":  {"type": "integer", "description": "Results to return (default 3)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hq_list_models",
            "description": "List all Ollama models installed on simon-hq and which are warm in RAM.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ── HQ availability (cached, non-blocking) ────────────────────

async def _check_hq_health() -> bool:
    global _hq_available, _hq_last_check
    now = time.time()
    if now - _hq_last_check < _hq_check_interval:
        return _hq_available
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{HQ_URL}/health")
            _hq_available = r.status_code == 200 and r.json().get("ollama") is True
    except Exception:
        _hq_available = False
    _hq_last_check = now
    return _hq_available


def hq_is_available() -> bool:
    return _hq_available and (time.time() - _hq_last_check < _hq_check_interval * 3)


# ── Memory sync ───────────────────────────────────────────────

async def sync_kb_to_hq():
    try:
        from simon_kb import memory_dump
        facts = memory_dump()
        if not facts:
            return
        payload = {"memories": facts, "collection": "simon_memory", "api_key": HQ_KEY}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{HQ_URL}/memory/sync", json=payload)
            if r.status_code == 200:
                count = r.json().get("synced", 0)
                print(f"[HQ Bridge] KB sync → HQ: {count} facts pushed to ChromaDB")
    except Exception as e:
        print(f"[HQ Bridge] KB sync failed (non-fatal): {e}")


def _sync_loop():
    time.sleep(15)
    while not _sync_stop.is_set():
        try:
            asyncio.run(sync_kb_to_hq())
        except Exception as e:
            print(f"[HQ Bridge] Sync loop error: {e}")
        _sync_stop.wait(300)


# ── Startup handshake — NON-BLOCKING ─────────────────────────
# CRITICAL: This runs as a fire-and-forget background task.
# It does NOT block SIMON startup. Each attempt has a 4s timeout.
# SIMON greets the owner immediately. HQ comes online in background.

async def _startup_handshake():
    global _hq_available, _hq_last_check
    from simon_kb import memory_dump

    print("[HQ Bridge] Background handshake started (non-blocking)")
    for attempt in range(20):   # try for up to ~10 minutes, quietly
        try:
            facts = memory_dump()
            payload = {
                "mac_api_url":  MAC_TS_URL,
                "mac_api_key":  HQ_KEY,
                "api_key":      HQ_KEY,
                "memory_count": len(facts) if facts else 0,
            }
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.post(f"{HQ_URL}/startup", json=payload)
                if r.status_code == 200:
                    data = r.json()
                    _hq_available  = True
                    _hq_last_check = time.time()
                    model = data.get("default_model", HQ_MODEL)
                    warm  = data.get("models_warm", [])
                    print(f"[HQ Bridge] ✅ HQ online | model={model} | warm={warm}")
                    await sync_kb_to_hq()
                    return
        except Exception:
            pass   # silent — don't spam the log on expected HQ-offline boots
        await asyncio.sleep(30)

    print("[HQ Bridge] HQ unreachable — Cloud fallback remains active")


# ── Plugin init ───────────────────────────────────────────────

async def _deferred_init():
    """Deferred init: runs after FastAPI app is fully initialized in lifespan."""
    await asyncio.sleep(0.5)  # yield to let app finish mounting all routes
    _register_callback_endpoint()
    asyncio.create_task(_startup_handshake())


def _on_load():
    global _sync_thread

    # NOTE: _register_callback_endpoint() is intentionally NOT called here.
    # It used to cause a circular import warning because jarvis.app wasn't
    # ready yet at plugin load time. It's now deferred via _deferred_init()
    # which jarvis.py schedules during its lifespan startup.

    _sync_stop.clear()
    _sync_thread = threading.Thread(
        target=_sync_loop, daemon=True, name="hq-kb-sync"
    )
    _sync_thread.start()
    print("[HQ Bridge] KB sync thread started (every 5 minutes)")

    # Schedule deferred init — runs after event loop is live
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_deferred_init())
        else:
            threading.Thread(
                target=lambda: asyncio.run(_deferred_init()),
                daemon=True, name="hq-deferred-init"
            ).start()
    except Exception as e:
        print(f"[HQ Bridge] Could not schedule deferred init: {e}")


def _register_callback_endpoint():
    try:
        import jarvis as _jarvis
        from fastapi import Request as _Request

        @_jarvis.app.post("/api/hq_tool")
        async def hq_tool_callback(request: _Request):
            try:
                body   = await request.json()
                hq_key = body.get("hq_key", "")
                tool   = body.get("tool", "")
                args   = body.get("args", {})
                if hq_key != HQ_KEY:
                    return {"status": "error", "detail": "Invalid HQ key"}
                ALLOWED = {
                    "remember", "recall", "get_todays_events",
                    "get_upcoming_events", "get_system_status",
                    "get_unread_emails", "get_reminders",
                }
                if tool not in ALLOWED:
                    return {"status": "error", "detail": f"Tool '{tool}' not allowed"}
                result = await _jarvis.execute_tool({"function": {"name": tool, "arguments": args}})
                return {"status": "ok", "result": result}
            except Exception as e:
                return {"status": "error", "detail": str(e)}

        print("[HQ Bridge] /api/hq_tool callback endpoint registered")
    except Exception as e:
        print(f"[HQ Bridge] Could not register callback endpoint: {e}")


_on_load()


# ── HTTP helpers ──────────────────────────────────────────────

async def _hq_get(path: str, timeout: int = TIMEOUT_FAST) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(f"{HQ_URL}{path}")
        r.raise_for_status()
        return r.json()


async def _hq_post(path: str, payload: dict, timeout: int = TIMEOUT_FAST) -> dict:
    payload["api_key"] = HQ_KEY
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{HQ_URL}{path}", json=payload)
        r.raise_for_status()
        return r.json()


def _fmt_search_results(results: list) -> str:
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        title   = r.get("title", "No title")
        snippet = r.get("snippet", "")
        url     = r.get("url", "")
        lines.append(f"{i}. {title}. {snippet}" + (f" ({url})" if url else ""))
    return " | ".join(lines)


# ── Dispatcher ────────────────────────────────────────────────

async def execute(name: str, args: dict) -> Optional[str]:

    if name == "hq_status":
        try:
            d = await _hq_get("/health", timeout=8)
            ollama   = "online" if d.get("ollama")   else "offline"
            chroma   = "online" if d.get("chromadb") else "offline"
            cpu      = d.get("cpu_pct", "?")
            ram_used = d.get("ram_used_gb", "?")
            ram_tot  = d.get("ram_total_gb", "?")
            warm     = d.get("models_warm", [])
            uptime   = d.get("uptime_hours", "?")
            warm_str = ", ".join(warm) if warm else "none loaded yet"
            return (f"simon-hq: online, ready. Ollama {ollama}, ChromaDB {chroma}. "
                    f"CPU {cpu}%, RAM {ram_used}GB of {ram_tot}GB. "
                    f"Uptime {uptime}h. Models warm: {warm_str}.")
        except httpx.ConnectError:
            return "Cannot reach simon-hq — check Tailscale or that HQ is powered on."
        except Exception as e:
            return f"HQ status error: {e}"

    elif name == "hq_ask":
        prompt = args.get("prompt", "").strip()
        model  = args.get("model", HQ_MODEL)
        if not prompt:
            return "No prompt provided."
        try:
            d = await _hq_post("/llm/chat", {
                "model":    model,
                "messages": [{"role": "user", "content": prompt}],
            }, timeout=TIMEOUT_CHAT)
            msg     = d.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            if not content:
                content = d.get("response", "")
            return content[:800] if len(content) > 800 else content
        except httpx.TimeoutException:
            return "HQ LLM timed out — model warming up, try again in a moment."
        except httpx.ConnectError:
            return "Cannot reach simon-hq — Tailscale may be disconnected."
        except Exception as e:
            return f"HQ LLM error: {e}"

    elif name == "hq_web_search":
        query       = args.get("query", "").strip()
        max_results = int(args.get("max_results", 5))
        if not query:
            return "No search query provided."
        try:
            d       = await _hq_post("/web/search", {"query": query, "max_results": max_results})
            results = d.get("results", [])
            if not results:
                return f"No web results for: {query}"
            return f"Web search '{query}': " + _fmt_search_results(results)
        except httpx.ConnectError:
            return "Cannot reach simon-hq for web search."
        except Exception as e:
            return f"Web search error: {e}"

    elif name == "hq_scrape":
        url     = args.get("url", "").strip()
        extract = args.get("extract", "text")
        if not url or not url.startswith("http"):
            return "A valid URL starting with http is required."
        try:
            d     = await _hq_post("/web/scrape", {"url": url, "extract": extract},
                                   timeout=TIMEOUT_SCRAPE)
            text  = d.get("text", d.get("html", ""))
            trunc = " (truncated)" if d.get("truncated") else ""
            return f"Scraped {url}{trunc}: {text[:600]}"
        except httpx.ConnectError:
            return "Cannot reach simon-hq for scraping."
        except Exception as e:
            return f"Scrape error: {e}"

    elif name == "hq_memory_store":
        document   = args.get("document", "").strip()
        collection = args.get("collection", "simon_memory")
        metadata   = args.get("metadata", {})
        if not document:
            return "No document text provided."
        try:
            d      = await _hq_post("/memory/store", {
                "document": document, "collection": collection, "metadata": metadata,
            })
            doc_id = d.get("id", "?")
            return f"Stored in HQ ChromaDB collection '{collection}', ID {doc_id}."
        except Exception as e:
            return f"HQ memory store error: {e}"

    elif name == "hq_memory_search":
        query      = args.get("query", "").strip()
        collection = args.get("collection", "simon_memory")
        n_results  = int(args.get("n_results", 3))
        if not query:
            return "No search query provided."
        try:
            d       = await _hq_post("/memory/search", {
                "query": query, "collection": collection, "n_results": n_results,
            })
            results = d.get("results", [])
            if not results:
                return f"Nothing found in '{collection}' for: {query}"
            lines = [f"{i}. [{r.get('distance','?')}] {r.get('text','')[:200]}"
                     for i, r in enumerate(results, 1)]
            return f"HQ memory search '{query}': " + " | ".join(lines)
        except Exception as e:
            return f"HQ memory search error: {e}"

    elif name == "hq_list_models":
        try:
            d      = await _hq_get("/ollama/models", timeout=8)
            models = d.get("models", [])
            warm   = d.get("warm", [])
            count  = d.get("count", len(models))
            if not models:
                return "No models found on simon-hq."
            warm_labels = [f"{m} (warm)" if m in warm else m for m in models]
            return f"simon-hq has {count} model{'s' if count != 1 else ''}: {', '.join(warm_labels)}."
        except Exception as e:
            return f"HQ model list error: {e}"

    return None
