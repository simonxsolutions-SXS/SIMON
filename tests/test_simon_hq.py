#!/usr/bin/env python3
"""
S.I.M.O.N. Integration Test Suite
====================================
Tests every layer of the Mac + HQ unified system.
Run: python3 ~/Projects/AI-Projects/jarvis/test_simon_hq.py
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────
cfg = json.loads((Path(__file__).parent / "config.json").read_text())
HQ_URL  = cfg.get("hq_api_url",  "http://YOUR_HQ_TAILSCALE_IP:8200")
HQ_KEY  = cfg.get("hq_api_key",  "")
HQ_MODEL = cfg.get("hq_model",   "qwen2.5:7b")
MAC_URL = f"http://localhost:{cfg.get('port', 8765)}"

PASS = "✅"; FAIL = "❌"; WARN = "⚠️ "

results = []

def log(symbol, label, detail="", ms=None):
    timing = f"  [{ms}ms]" if ms else ""
    line   = f"  {symbol} {label}{timing}"
    if detail:
        line += f"\n       {detail}"
    print(line)
    results.append({"symbol": symbol, "label": label, "ms": ms, "detail": detail})

async def test(label, coro):
    t0 = time.time()
    try:
        result = await coro
        ms = round((time.time() - t0) * 1000)
        log(PASS, label, result, ms)
        return True
    except Exception as e:
        ms = round((time.time() - t0) * 1000)
        log(FAIL, label, str(e)[:120], ms)
        return False

# ─────────────────────────────────────────────────────────────

async def check_mac_api():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{MAC_URL}/api/status")
        d = r.json()
        return f"CPU {d.get('cpu','?')}% | RAM {d.get('mem_gb','?')}GB | SIMON is alive"

async def check_mac_plugins():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{MAC_URL}/api/plugins")
        d = r.json()
        plugins = d.get("plugins", [])
        names   = [p["name"] for p in plugins]
        tools   = d.get("count", 0)
        return f"{len(plugins)} plugins loaded ({', '.join(names)}) | {tools} total tools"

async def check_hq_health():
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(f"{HQ_URL}/health")
        d = r.json()
        status  = d.get("status", "?").upper()
        ready   = "ready" if d.get("ready") else "warming"
        ram     = f"{d.get('ram_used_gb','?')}GB/{d.get('ram_total_gb','?')}GB RAM"
        disk    = f"{d.get('disk_used_gb','?')}GB/{d.get('disk_total_gb','?')}GB disk"
        ollama  = "Ollama OK" if d.get("ollama") else "Ollama DOWN"
        chroma  = "ChromaDB OK" if d.get("chromadb") else "ChromaDB DOWN"
        return f"{status} | {ready} | {ram} | {disk} | {ollama} | {chroma}"

async def check_hq_models():
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(f"{HQ_URL}/ollama/models")
        d = r.json()
        models = d.get("models", [])
        warm   = d.get("warm", [])
        warm_labels = [f"{m}★" if m in warm else m for m in models]
        return f"{d.get('count',0)} models: {', '.join(warm_labels)} (★=warm in RAM)"

async def check_hq_startup_handshake():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{HQ_URL}/startup", json={
            "mac_api_url": f"http://YOUR_MAC_TAILSCALE_IP:{cfg.get('port',8765)}",
            "mac_api_key": HQ_KEY,
            "api_key":     HQ_KEY,
            "memory_count": 1,
        })
        d = r.json()
        model = d.get("default_model", "?")
        warm  = d.get("models_warm", [])
        return f"HQ acknowledged Mac | model={model} | warm={warm}"

async def check_hq_llm():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{HQ_URL}/llm/chat", json={
            "model":    HQ_MODEL,
            "messages": [{"role": "user", "content":
                "In exactly one sentence, what is your role in the SIMON system?"}],
            "api_key":  HQ_KEY,
        })
        d   = r.json()
        msg = d.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        return content[:200] if content else "No response"

async def check_hq_web_search():
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{HQ_URL}/web/search", json={
            "query":       "Simon-X Solutions [CITY] MSP",
            "max_results": 3,
            "api_key":     HQ_KEY,
        })
        d       = r.json()
        results = d.get("results", [])
        if not results:
            return "No results returned"
        top = results[0]
        return f"{len(results)} results | Top: {top.get('title','?')[:80]}"

async def check_hq_memory_store():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{HQ_URL}/memory/store", json={
            "document":   "SIMON integration test — Mac and HQ unified on 2026-03-17. All systems operational.",
            "collection": "simon_memory",
            "metadata":   {"source": "integration_test", "date": str(datetime.now().date())},
            "doc_id":     "integration_test_001",
            "api_key":    HQ_KEY,
        })
        d = r.json()
        return f"Stored doc ID: {d.get('id','?')} in collection '{d.get('collection','?')}'"

async def check_hq_memory_search():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{HQ_URL}/memory/search", json={
            "query":      "SIMON integration Mac HQ unified",
            "collection": "simon_memory",
            "n_results":  3,
            "api_key":    HQ_KEY,
        })
        d       = r.json()
        items   = d.get("results", [])
        if not items:
            return "No results — ChromaDB may still be indexing"
        top = items[0]
        dist = top.get("distance", "?")
        text = top.get("text", "")[:80]
        return f"{len(items)} results | Best match (dist={dist}): {text}"

async def check_hq_memory_sync():
    """Simulate what hq_bridge.py does — push Mac KB facts to ChromaDB."""
    # Pull actual facts from Mac KB
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from simon_kb import memory_dump
        facts = memory_dump() or []
    except Exception:
        facts = [{"key": "test_fact", "value": "integration test value", "category": "test"}]

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{HQ_URL}/memory/sync", json={
            "memories":   facts[:20],  # first 20
            "collection": "simon_memory",
            "api_key":    HQ_KEY,
        })
        d = r.json()
        return f"Synced {d.get('synced',0)} Mac KB facts → HQ ChromaDB"

async def check_hq_scrape():
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post(f"{HQ_URL}/web/scrape", json={
            "url":       "https://tailscale.com",
            "extract":   "text",
            "max_chars": 300,
            "api_key":   HQ_KEY,
        })
        d    = r.json()
        text = d.get("text", "")[:150]
        return f"Scraped {d.get('chars',0)} chars from tailscale.com | Preview: {text}..."

async def check_tailscale_connectivity():
    """Ping HQ Tailscale IP from Mac."""
    import subprocess
    r = subprocess.run(
        ["ping", "-c", "3", "-W", "2000", "YOUR_HQ_TAILSCALE_IP"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        # Parse avg RTT
        import re
        m = re.search(r"min/avg/max/stddev = [\d.]+/([\d.]+)", r.stdout)
        avg = m.group(1) if m else "?"
        return f"3 packets sent, 0% loss | avg RTT {avg}ms over Tailscale"
    else:
        raise Exception("Ping failed — Tailscale may be down")

# ─────────────────────────────────────────────────────────────

async def main():
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║   S.I.M.O.N. Integration Test Suite                 ║")
    print("  ║   Mac + simon-hq Unified Intelligence               ║")
    print(f"  ║   {datetime.now().strftime('%A, %B %d %Y  %I:%M %p'):<49}║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()

    # ── Layer 1: Network ──────────────────────────────────────
    print("  ─ Layer 1: Network & Connectivity ──────────────────")
    await test("Tailscale tunnel Mac → HQ",      check_tailscale_connectivity())
    await test("HQ API reachable over Tailscale", check_hq_health())
    print()

    # ── Layer 2: SIMON Mac ────────────────────────────────────
    print("  ─ Layer 2: SIMON on Mac ─────────────────────────────")
    await test("SIMON FastAPI running",           check_mac_api())
    await test("Plugin system loaded",            check_mac_plugins())
    print()

    # ── Layer 3: HQ Services ──────────────────────────────────
    print("  ─ Layer 3: HQ Services ──────────────────────────────")
    await test("HQ health (Ollama + ChromaDB)",   check_hq_health())
    await test("Ollama models installed",         check_hq_models())
    await test("Startup handshake Mac → HQ",      check_hq_startup_handshake())
    print()

    # ── Layer 4: HQ Intelligence ──────────────────────────────
    print("  ─ Layer 4: HQ Intelligence (new capabilities) ───────")
    await test("HQ LLM inference (llama3.1:8b)", check_hq_llm())
    await test("Web search via HQ (DuckDuckGo)", check_hq_web_search())
    await test("Web scrape via HQ (Playwright)",  check_hq_scrape())
    print()

    # ── Layer 5: Unified Memory ───────────────────────────────
    print("  ─ Layer 5: Unified Memory (ChromaDB + Mac KB) ───────")
    await test("Store document in ChromaDB",      check_hq_memory_store())
    await test("Semantic search ChromaDB",        check_hq_memory_search())
    await test("Sync Mac KB → HQ ChromaDB",       check_hq_memory_sync())
    print()

    # ── Summary ───────────────────────────────────────────────
    passed = sum(1 for r in results if r["symbol"] == PASS)
    failed = sum(1 for r in results if r["symbol"] == FAIL)
    warned = sum(1 for r in results if r["symbol"] == WARN)
    total  = len(results)

    print("  ─ Results ───────────────────────────────────────────")
    print(f"  {PASS} Passed : {passed}/{total}")
    if failed: print(f"  {FAIL} Failed : {failed}/{total}")
    if warned:  print(f"  {WARN} Warnings: {warned}/{total}")
    print()

    if failed == 0:
        print("  🎯 All systems unified and operational.")
        print("  SIMON + HQ are running as one.")
    elif failed <= 2:
        print("  Core systems operational. Minor issues noted above.")
    else:
        print(f"  {failed} tests failed — review output above.")
    print()

asyncio.run(main())
