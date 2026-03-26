#!/usr/bin/env python3
"""
S.I.M.O.N. HQ Deep Diagnostic
Hits each HQ endpoint directly and prints the raw response
so we can see exactly what's coming back and why.
"""

import asyncio
import json
from pathlib import Path
import httpx

cfg     = json.loads((Path(__file__).parent / "config.json").read_text())
HQ_URL  = cfg.get("hq_api_url", "http://YOUR_HQ_TAILSCALE_IP:8200")
HQ_KEY  = cfg.get("hq_api_key", "")
HQ_MODEL = cfg.get("hq_model", "qwen2.5:7b")

def section(title):
    print(f"\n  {'─'*50}")
    print(f"  {title}")
    print(f"  {'─'*50}")

async def main():
    print("\n  S.I.M.O.N. HQ Deep Diagnostic\n")

    async with httpx.AsyncClient(timeout=90) as c:

        # ── 1. Raw health ────────────────────────────────────
        section("1. Raw /health response")
        r = await c.get(f"{HQ_URL}/health")
        print(f"  Status code : {r.status_code}")
        print(f"  Body        : {json.dumps(r.json(), indent=4)}")

        # ── 2. Raw models list ───────────────────────────────
        section("2. Raw /ollama/models response")
        r = await c.get(f"{HQ_URL}/ollama/models")
        print(f"  Status code : {r.status_code}")
        print(f"  Body        : {json.dumps(r.json(), indent=4)}")

        # ── 3. Raw LLM chat ──────────────────────────────────
        section("3. Raw /llm/chat response (may take 30-60s first call)")
        print(f"  Sending to model: {HQ_MODEL}")
        try:
            r = await c.post(f"{HQ_URL}/llm/chat", json={
                "model":    HQ_MODEL,
                "messages": [{"role": "user", "content": "Say the word ONLINE and nothing else."}],
                "api_key":  HQ_KEY,
            })
            print(f"  Status code : {r.status_code}")
            raw = r.json()
            print(f"  Full body   : {json.dumps(raw, indent=4)}")
        except Exception as e:
            print(f"  ERROR: {e}")

        # ── 4. Raw web search ────────────────────────────────
        section("4. Raw /web/search response")
        try:
            r = await c.post(f"{HQ_URL}/web/search", json={
                "query":       "[CITY] MSP IT services",
                "max_results": 2,
                "api_key":     HQ_KEY,
            })
            print(f"  Status code : {r.status_code}")
            print(f"  Body        : {json.dumps(r.json(), indent=4)}")
        except Exception as e:
            print(f"  ERROR: {e}")

        # ── 5. Raw memory store ──────────────────────────────
        section("5. Raw /memory/store response")
        try:
            r = await c.post(f"{HQ_URL}/memory/store", json={
                "document":   "diagnostic test document stored at " + str(__import__('datetime').datetime.now()),
                "collection": "simon_memory",
                "doc_id":     "diag_001",
                "api_key":    HQ_KEY,
            })
            print(f"  Status code : {r.status_code}")
            print(f"  Body        : {json.dumps(r.json(), indent=4)}")
        except Exception as e:
            print(f"  ERROR: {e}")

        # ── 6. Raw memory search ─────────────────────────────
        section("6. Raw /memory/search response")
        try:
            r = await c.post(f"{HQ_URL}/memory/search", json={
                "query":      "diagnostic test",
                "collection": "simon_memory",
                "n_results":  3,
                "api_key":    HQ_KEY,
            })
            print(f"  Status code : {r.status_code}")
            print(f"  Body        : {json.dumps(r.json(), indent=4)}")
        except Exception as e:
            print(f"  ERROR: {e}")

        # ── 7. Auth check ────────────────────────────────────
        section("7. Auth check — what key is HQ expecting?")
        print(f"  Key in config.json : {HQ_KEY[:8]}...{HQ_KEY[-4:]} ({len(HQ_KEY)} chars)")
        # Try with wrong key to confirm auth is enforced
        try:
            r = await c.post(f"{HQ_URL}/llm/chat", json={
                "model":    HQ_MODEL,
                "messages": [{"role": "user", "content": "test"}],
                "api_key":  "wrong-key",
            })
            if r.status_code == 403:
                print(f"  Auth working: 403 returned for wrong key ✅")
            else:
                print(f"  Auth check returned {r.status_code} — auth may not be enforced")
        except Exception as e:
            print(f"  Auth check error: {e}")

        # ── 8. HQ service logs via API ───────────────────────
        section("8. Startup handshake raw response")
        try:
            r = await c.post(f"{HQ_URL}/startup", json={
                "mac_api_url":  "http://YOUR_MAC_TAILSCALE_IP:8765",
                "mac_api_key":  HQ_KEY,
                "api_key":      HQ_KEY,
                "memory_count": 5,
            })
            print(f"  Status code : {r.status_code}")
            print(f"  Body        : {json.dumps(r.json(), indent=4)}")
        except Exception as e:
            print(f"  ERROR: {e}")

    print("\n  Diagnostic complete.\n")

asyncio.run(main())
