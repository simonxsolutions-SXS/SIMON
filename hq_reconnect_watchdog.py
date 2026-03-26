#!/usr/bin/env python3
"""
hq_reconnect_watchdog.py — runs on Mac, checks HQ every 60s,
forces SIMON's _bg_hq_health to re-trigger when HQ comes back.
This is a standalone process — run it in background alongside SIMON.

Usage: /opt/homebrew/bin/python3.11 hq_reconnect_watchdog.py &
Or add to start_simon.sh (already done if you re-run start_simon.sh)
"""
import asyncio, time, httpx

HQ_URL     = "http://YOUR_HQ_TAILSCALE_IP:8200"
SIMON_URL  = "http://localhost:8765"
CHECK_SECS = 60

async def check_loop():
    was_online = False
    print("[HQ Watchdog] Started — checking HQ every 60s")
    while True:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{HQ_URL}/health")
                is_online = r.status_code == 200 and r.json().get("ollama") is True
        except Exception:
            is_online = False

        if is_online and not was_online:
            print(f"[HQ Watchdog] ✅ HQ came ONLINE — notifying SIMON")
            # Ping SIMON's API to trigger a stats refresh which also checks HQ
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    await c.get(f"{SIMON_URL}/api/hq_health")
            except Exception:
                pass
        elif not is_online and was_online:
            print(f"[HQ Watchdog] ⚠️  HQ went OFFLINE — waiting for recovery")

        was_online = is_online
        await asyncio.sleep(CHECK_SECS)

if __name__ == "__main__":
    asyncio.run(check_loop())
