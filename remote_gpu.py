# remote_gpu.py
import os
from typing import Optional

import httpx

HQ_URL = os.environ.get("SIMON_HQ_URL", "http://YOUR_HQ_TAILSCALE_IP:8000")
HQ_TOKEN = os.environ.get("SIMON_HQ_TOKEN", "CHANGE_ME")

_client: Optional[httpx.AsyncClient] = None

async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=60)
    return _client

async def hq_health() -> dict:
    client = await _get_client()
    r = await client.get(f"{HQ_URL}/health", headers={"X-Simon-Token": HQ_TOKEN})
    r.raise_for_status()
    return r.json()

async def hq_chat(prompt: str, system: Optional[str] = None,
                  model: Optional[str] = None,
                  max_tokens: int = 512,
                  temperature: float = 0.3) -> str:
    client = await _get_client()
    payload = {
        "prompt": prompt,
        "system": system,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    r = await client.post(
        f"{HQ_URL}/gpu/chat",
        json=payload,
        headers={"X-Simon-Token": HQ_TOKEN},
    )
    r.raise_for_status()
    data = r.json()
    return data.get("text", "")
