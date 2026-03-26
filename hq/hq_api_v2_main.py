#!/usr/bin/env python3
"""
S.I.M.O.N. HQ API Bridge — main.py  (v2.1 — Vision Added)
====================================================================
Simon-X Solutions | [OWNER_NAME]

v2.1 changes:
  - /vision/ask endpoint: Mac captures frame, sends base64 to HQ,
    llama3.2-vision:11b answers the question properly.
    This fixes finger counting, text reading, precise scene analysis.
    Moondream on Mac stays for fast object detection (YOLO).
    HQ vision handles anything requiring real understanding.

Architecture:
  Mac (SIMON)  ←──Tailscale──→  HQ (simon-hq)
  Voice/TTS/camera               Ollama LLM + vision
  AppleScript tools              ChromaDB memory
  YOLO fast detection            llama3.2-vision:11b
  SQLite KB                      Web scraping

Ports:
  8200  HQ API (this file)
  8100  ChromaDB (separate service)
  11434 Ollama (separate service)
"""

import asyncio
import base64
import hmac
import ipaddress
import json
import os
import socket
import time
import threading
from datetime import datetime
from typing import Optional, AsyncGenerator
from urllib.parse import urlparse

import re as _re

import chromadb
import httpx
import psutil
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────
OLLAMA_URL     = os.getenv("OLLAMA_URL",       "http://localhost:11434")
CHROMA_HOST    = os.getenv("CHROMA_HOST",      "localhost")
CHROMA_PORT    = int(os.getenv("CHROMA_PORT",  "8100"))
DEFAULT_MODEL  = os.getenv("SIMON_HQ_MODEL",   "qwen2.5:7b")
VISION_MODEL   = os.getenv("SIMON_VISION_MODEL","llama3.2-vision:11b")
API_KEY        = os.getenv("SIMON_HQ_KEY",     "simon-hq-key-changeme")
HQ_PORT        = int(os.getenv("HQ_API_PORT",  "8200"))
HQ_HOST        = os.getenv("HQ_API_HOST",      "127.0.0.1")   # override with Tailscale IP
MAC_API_URL    = os.getenv("MAC_API_URL",      "http://YOUR_MAC_TAILSCALE_IP:8765")

# ── Default-key guard ─────────────────────────────────────────
_DEFAULT_KEY = "simon-hq-key-changeme"
if API_KEY == _DEFAULT_KEY:
    import sys
    print(
        "[HQ FATAL] SIMON_HQ_KEY is the default placeholder. "
        "Set a real secret in /etc/systemd/system/simon-hq-api.service "
        "or via EnvironmentFile. Refusing to start.",
        file=sys.stderr,
    )
    sys.exit(1)
MAC_API_KEY    = os.getenv("MAC_API_KEY",      "")

app = FastAPI(title="S.I.M.O.N. HQ API", version="2.1")

# ── Tool output sanitization (prompt injection defense) ───────────
_INJECT_PATTERNS = _re.compile(
    r'(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?'
    r'|new\s+instructions?:'
    r'|system\s*:\s*you\s+are\s+now'
    r'|<\s*/?system\s*>'
    r'|<\s*/?instructions?\s*>'
    r'|<\s*/?prompt\s*>'
    r'|assistant\s*:\s*i\s+will\s+now'
    r'|forget\s+(everything|your\s+instructions?)'
    r'|do\s+not\s+follow\s+your\s+(previous\s+)?instructions?'
    r'|\bDAN\b.*mode'
    r'|jailbreak)',
    _re.IGNORECASE | _re.MULTILINE
)


def _sanitize(text: str, source: str = "") -> str:
    """Strip prompt injection patterns from text before returning to LLM."""
    if _INJECT_PATTERNS.search(text):
        print(f"[HQ SECURITY] Prompt injection detected in output from {source or 'web'}", flush=True)
        return _INJECT_PATTERNS.sub("[⚠ PROMPT INJECTION ATTEMPT REMOVED]", text)
    return text

# ── State ─────────────────────────────────────────────────────
_chroma:       Optional[chromadb.HttpClient] = None
_hq_ready      = False
_models_warm   = set()
_last_mac_seen = None
_startup_time  = datetime.now()

def get_chroma():
    global _chroma
    if _chroma is None:
        _chroma = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return _chroma

def _auth(provided: str) -> bool:
    """Constant-time API key comparison. Fail-closed — empty key never passes."""
    if not provided or not API_KEY:
        return False
    return hmac.compare_digest(provided.encode(), API_KEY.encode())


# ── SSRF protection ───────────────────────────────────────────
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_ssrf_safe(url: str) -> tuple[bool, str]:
    """
    Returns (True, "") if URL is safe to fetch, or (False, reason) if SSRF risk.
    Blocks: private RFC1918, loopback, link-local, cloud metadata (169.254.169.254).
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Scheme '{parsed.scheme}' not allowed (only http/https)."
        hostname = parsed.hostname
        if not hostname:
            return False, "No hostname in URL."
        # Resolve to IP(s) — catches DNS rebinding to some extent
        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"
        for _fam, _type, _proto, _canon, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_private:
                return False, f"URL resolves to private/loopback/link-local address: {ip_str}"
            for net in _PRIVATE_NETS:
                if ip in net:
                    return False, f"URL resolves to blocked network: {ip_str}"
        return True, ""
    except Exception as e:
        return False, f"URL validation error: {e}"

# ── Request models ────────────────────────────────────────────
class ChatRequest(BaseModel):
    model:       str   = DEFAULT_MODEL
    messages:    list
    stream:      bool  = False
    tools:       list  = []
    system:      str   = ""
    api_key:     str   = ""
    temperature: float = 0.7

class VisionRequest(BaseModel):
    """
    Mac captures a camera frame, encodes it as base64 JPEG,
    sends it here with a question. llama3.2-vision answers it.
    """
    image_b64:   str          # base64-encoded JPEG frame from Mac camera
    question:    str          # natural language question about the image
    model:       str   = ""   # override vision model (default: llama3.2-vision:11b)
    api_key:     str   = ""

class EmbedRequest(BaseModel):
    model:   str = "nomic-embed-text"
    text:    str
    api_key: str = ""

class MemoryStore(BaseModel):
    collection: str  = "simon_memory"
    document:   str
    metadata:   dict = {}
    doc_id:     str  = ""
    api_key:    str  = ""

class MemorySearch(BaseModel):
    collection: str = "simon_memory"
    query:      str
    n_results:  int = 5
    api_key:    str = ""

class MemorySync(BaseModel):
    memories:   list
    collection: str = "simon_memory"
    api_key:    str = ""

class ScrapeRequest(BaseModel):
    url:       str
    extract:   str = "text"
    max_chars: int = 8000
    api_key:   str = ""

class SearchRequest(BaseModel):
    query:       str
    max_results: int = 5
    api_key:     str = ""

class MacCallbackRequest(BaseModel):
    tool:    str
    args:    dict = {}
    api_key: str  = ""

class StartupRequest(BaseModel):
    mac_api_url:  str = ""
    mac_api_key:  str = ""
    api_key:      str = ""
    memory_count: int = 0

# ─────────────────────────────────────────────────────────────
#  STARTUP
# ─────────────────────────────────────────────────────────────

async def _prewarm_model(model: str):
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate",
                             json={"model": model, "prompt": "hi", "stream": False})
            if r.status_code == 200:
                _models_warm.add(model)
                print(f"[HQ] Model warm: {model}")
    except Exception as e:
        print(f"[HQ] Pre-warm failed for {model}: {e}")

async def _hq_startup_sequence():
    global _hq_ready
    await asyncio.sleep(5)
    await _prewarm_model(DEFAULT_MODEL)
    await _prewarm_model("nomic-embed-text")
    # Don't pre-warm vision model — it's 8GB, load on demand
    try:
        get_chroma().heartbeat()
        print("[HQ] ChromaDB connected")
    except Exception as e:
        print(f"[HQ] ChromaDB not ready: {e}")
    _hq_ready = True
    print("[HQ] Startup complete — ready for SIMON")

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(_hq_startup_sequence())

# ─────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    global _last_mac_seen
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        pass
    chroma_ok = False
    try:
        get_chroma().heartbeat()
        chroma_ok = True
    except Exception:
        pass
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return JSONResponse({
        "status":        "ok" if (ollama_ok and chroma_ok) else "degraded",
        "ready":         _hq_ready,
        "timestamp":     datetime.now().isoformat(),
        "uptime_hours":  round((datetime.now() - _startup_time).total_seconds() / 3600, 1),
        "ollama":        ollama_ok,
        "chromadb":      chroma_ok,
        "models_warm":   list(_models_warm),
        "vision_model":  VISION_MODEL,
        "cpu_pct":       psutil.cpu_percent(interval=0.2),
        "ram_pct":       mem.percent,
        "ram_used_gb":   round(mem.used  / 1e9, 1),
        "ram_total_gb":  round(mem.total / 1e9, 1),
        "disk_used_gb":  round(disk.used  / 1e9, 1),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "last_mac_ping": _last_mac_seen,
    })


@app.post("/startup")
async def startup_handshake(req: StartupRequest, background_tasks: BackgroundTasks):
    global MAC_API_URL, MAC_API_KEY, _last_mac_seen
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    if req.mac_api_url:
        MAC_API_URL = req.mac_api_url
    if req.mac_api_key:
        MAC_API_KEY = req.mac_api_key
    _last_mac_seen = datetime.now().isoformat()
    if req.memory_count > 0:
        background_tasks.add_task(_ensure_models_warm)
    return JSONResponse({
        "hq_ready":      _hq_ready,
        "models_warm":   list(_models_warm),
        "default_model": DEFAULT_MODEL,
        "vision_model":  VISION_MODEL,
        "ollama_url":    OLLAMA_URL,
        "chroma_ok":     True,
        "message":       "HQ online — ready to serve SIMON",
    })

async def _ensure_models_warm():
    if DEFAULT_MODEL not in _models_warm:
        await _prewarm_model(DEFAULT_MODEL)


@app.get("/ollama/models")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            return JSONResponse({"models": models, "count": len(models), "warm": list(_models_warm)})
    except Exception as e:
        raise HTTPException(502, f"Ollama unreachable: {e}")


@app.post("/llm/chat")
async def llm_chat(req: ChatRequest):
    global _last_mac_seen
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    _last_mac_seen = datetime.now().isoformat()
    messages = req.messages
    if req.system and (not messages or messages[0].get("role") != "system"):
        messages = [{"role": "system", "content": req.system}] + messages
    try:
        payload = {"model": req.model, "messages": messages, "stream": False,
                   "options": {"temperature": req.temperature}}
        if req.tools:
            payload["tools"] = req.tools
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            _models_warm.add(req.model)
            return JSONResponse(r.json())
    except httpx.TimeoutException:
        raise HTTPException(504, "Ollama timeout — model loading, retry in 10s")
    except Exception as e:
        raise HTTPException(502, f"LLM error: {e}")


@app.post("/vision/ask")
async def vision_ask(req: VisionRequest):
    """
    THE KEY ENDPOINT — Mac sends a camera frame + question, HQ answers with
    llama3.2-vision:11b. This is a real vision-language model, not Moondream.
    Handles: finger counting, text reading, object ID, scene analysis.

    Flow:
      1. Mac captures frame via OpenCV
      2. Encodes as base64 JPEG
      3. POSTs here with the question
      4. HQ runs llama3.2-vision:11b
      5. Returns the answer as text
    """
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")

    vision_model = req.model or VISION_MODEL

    # Validate base64 image
    if not req.image_b64:
        raise HTTPException(400, "image_b64 is required")

    try:
        # Ollama vision format: messages with images array
        payload = {
            "model":  vision_model,
            "messages": [
                {
                    "role":    "user",
                    "content": req.question,
                    "images":  [req.image_b64],   # base64 JPEG, no data: prefix
                }
            ],
            "stream": False,
            "options": {"temperature": 0.1},  # low temp for factual visual answers
        }

        print(f"[HQ Vision] {vision_model} — question: {req.question[:80]}")
        t0 = time.time()

        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()

        ms = round((time.time() - t0) * 1000)
        msg = data.get("message", {})
        answer = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        _models_warm.add(vision_model)

        print(f"[HQ Vision] Answer ({ms}ms): {answer[:120]}")

        return JSONResponse({
            "answer":  answer.strip(),
            "model":   vision_model,
            "ms":      ms,
            "question": req.question,
        })

    except httpx.TimeoutException:
        raise HTTPException(504, f"{vision_model} timed out — model loading, try again")
    except Exception as e:
        raise HTTPException(502, f"Vision error: {e}")


@app.post("/llm/embed")
async def llm_embed(req: EmbedRequest):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{OLLAMA_URL}/api/embeddings",
                             json={"model": req.model, "prompt": req.text})
            r.raise_for_status()
            return JSONResponse(r.json())
    except Exception as e:
        raise HTTPException(502, f"Embed error: {e}")


# ── Memory (ChromaDB) ─────────────────────────────────────────

@app.post("/memory/store")
async def memory_store(req: MemoryStore):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    try:
        col    = get_chroma().get_or_create_collection(req.collection)
        doc_id = req.doc_id or f"doc_{int(time.time()*1000)}"
        meta   = {**req.metadata, "stored_at": datetime.now().isoformat()}
        col.upsert(documents=[req.document], ids=[doc_id], metadatas=[meta])
        return JSONResponse({"stored": True, "id": doc_id, "collection": req.collection})
    except Exception as e:
        raise HTTPException(500, f"ChromaDB store error: {e}")


@app.post("/memory/search")
async def memory_search_route(req: MemorySearch):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    try:
        col     = get_chroma().get_or_create_collection(req.collection)
        results = col.query(query_texts=[req.query], n_results=min(req.n_results, 20))
        docs    = results.get("documents", [[]])[0]
        metas   = results.get("metadatas", [[]])[0]
        dists   = results.get("distances",  [[]])[0]
        items   = [{"text": d, "metadata": m, "distance": round(dist, 4)}
                   for d, m, dist in zip(docs, metas, dists)]
        return JSONResponse({"results": items, "count": len(items), "collection": req.collection})
    except Exception as e:
        raise HTTPException(500, f"ChromaDB search error: {e}")


@app.post("/memory/sync")
async def memory_sync(req: MemorySync):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    if not req.memories:
        return JSONResponse({"synced": 0})
    try:
        col = get_chroma().get_or_create_collection(req.collection)
        docs, ids, metas = [], [], []
        for m in req.memories:
            key   = m.get("key", "")
            value = m.get("value", "")
            if not key or not value:
                continue
            docs.append(f"{key}: {value}")
            ids.append(f"mac_kb_{key}")
            metas.append({"key": key, "category": m.get("category", "general"),
                          "source": m.get("source", "mac_kb"),
                          "synced_at": datetime.now().isoformat()})
        if docs:
            col.upsert(documents=docs, ids=ids, metadatas=metas)
        return JSONResponse({"synced": len(docs), "collection": req.collection})
    except Exception as e:
        raise HTTPException(500, f"Memory sync error: {e}")


@app.get("/memory/list")
async def memory_list():
    try:
        cols = get_chroma().list_collections()
        return JSONResponse({"collections": [{"name": c.name, "count": c.count()} for c in cols]})
    except Exception as e:
        raise HTTPException(500, f"ChromaDB list error: {e}")


# ── Mac Callback ──────────────────────────────────────────────

@app.post("/mac/callback")
async def mac_callback(req: MacCallbackRequest):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    if not MAC_API_URL:
        raise HTTPException(503, "Mac API URL not registered — SIMON must connect first")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{MAC_API_URL}/api/hq_tool",
                             json={"tool": req.tool, "args": req.args, "hq_key": API_KEY})
            r.raise_for_status()
            return JSONResponse(r.json())
    except httpx.ConnectError:
        raise HTTPException(503, "Cannot reach Mac — Tailscale may be down")
    except Exception as e:
        raise HTTPException(500, f"Mac callback error: {e}")


# ── Web tools ─────────────────────────────────────────────────

@app.post("/web/scrape")
async def web_scrape(req: ScrapeRequest):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    # SSRF guard — block private IPs, loopback, cloud metadata endpoints
    safe, reason = _is_ssrf_safe(req.url)
    if not safe:
        raise HTTPException(400, f"SSRF protection blocked this URL: {reason}")
    try:
        from playwright.async_api import async_playwright
        from bs4 import BeautifulSoup
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page    = await browser.new_page()
            await page.goto(req.url, wait_until="domcontentloaded", timeout=20000)
            content = await page.content()
            await browser.close()
        soup = BeautifulSoup(content, "lxml")
        for tag in soup(["script","style","nav","footer","header","aside","iframe"]):
            tag.decompose()
        if req.extract == "links":
            links = [a.get("href","") for a in soup.find_all("a", href=True)
                     if a.get("href","").startswith("http")][:50]
            return JSONResponse({"url": req.url, "links": links, "count": len(links)})
        text = "\n".join(l for l in soup.get_text(separator="\n", strip=True).splitlines() if l.strip())
        text = _sanitize(text, source=req.url)
        return JSONResponse({"url": req.url, "text": text[:req.max_chars],
                             "chars": min(len(text), req.max_chars),
                             "truncated": len(text) > req.max_chars})
    except Exception as e:
        raise HTTPException(500, f"Scrape error: {e}")


@app.post("/web/search")
async def web_search(req: SearchRequest):
    if not _auth(req.api_key):
        raise HTTPException(403, "Invalid API key")
    try:
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SIMON-HQ/2.1)"}
        async with httpx.AsyncClient(timeout=12, headers=headers) as c:
            r = await c.post("https://html.duckduckgo.com/html/", data={"q": req.query})
            r.raise_for_status()
        soup    = BeautifulSoup(r.text, "lxml")
        results = []
        for result in soup.select(".result")[:req.max_results]:
            t = result.select_one(".result__title")
            u = result.select_one(".result__url")
            s = result.select_one(".result__snippet")
            if t:
                snippet = s.get_text(strip=True) if s else ""
                results.append({"title": _sanitize(t.get_text(strip=True), "search:title"),
                                 "url":   u.get_text(strip=True) if u else "",
                                 "snippet": _sanitize(snippet, "search:snippet")})
        return JSONResponse({"query": req.query, "results": results, "count": len(results)})
    except Exception as e:
        raise HTTPException(500, f"Search error: {e}")


if __name__ == "__main__":
    print(f"""
  ╔══════════════════════════════════════════════════════╗
  ║   S.I.M.O.N. HQ API  v2.1  |  simon-hq             ║
  ║   LLM     : {DEFAULT_MODEL:<41}║
  ║   Vision  : {VISION_MODEL:<41}║
  ║   Port    : {HQ_PORT:<41}║
  ╚══════════════════════════════════════════════════════╝
    """)
    uvicorn.run(app, host=HQ_HOST, port=HQ_PORT, log_level="warning")
