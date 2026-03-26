"""
S.I.M.O.N. Plugin — LM Studio Bridge (v1.0)
=============================================
Simon-X Solutions | [OWNER_NAME]

Connects SIMON to LM Studio's OpenAI-compatible API running on simon-hq.
LM Studio gives you access to any GGUF model with full GPU acceleration,
a clean UI for managing models, and an OpenAI-compatible REST server.

SETUP (one-time):
  1. Open LM Studio on simon-hq
  2. Load any model you want to use
  3. Click the "<-> API" tab (or "Developer" tab) on the left sidebar
  4. Click "Start Server" — it runs on port 1234 by default
  5. Say "Simon, LM Studio status" to verify SIMON can see it

Tools:
  lm_status          — Check if LM Studio server is running and what model is loaded
  lm_ask             — Send a prompt to the currently loaded LM Studio model
  lm_list_models     — List all models available in LM Studio
  lm_load_model      — Request a specific model to be loaded (triggers LM Studio to load it)
  lm_compare         — Send the same prompt to both HQ Ollama and LM Studio and compare responses

Voice commands:
  "Simon, LM Studio status"
  "Simon, ask LM Studio what you think about network segmentation"
  "Simon, what models does LM Studio have?"
  "Simon, compare HQ and LM Studio on this question: what is zero trust?"
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

import httpx

# ─────────────────────────────────────────────────────────────────────────────

METADATA = {
    "name":        "LM Studio",
    "description": "Interface to LM Studio's local AI server on simon-hq — GPU-accelerated model inference",
    "version":     "1.0",
    "author":      "Simon-X Solutions",
}

_cfg_path = Path(__file__).parent.parent / "config.json"
try:
    _cfg = json.loads(_cfg_path.read_text())
except Exception:
    _cfg = {}

# LM Studio default: OpenAI-compatible API on port 1234
# Can be overridden in config.json as "lm_studio_url"
LM_URL      = _cfg.get("lm_studio_url",    "http://YOUR_HQ_TAILSCALE_IP:1234")
HQ_URL      = _cfg.get("hq_api_url",       "http://YOUR_HQ_TAILSCALE_IP:8200")
HQ_KEY      = _cfg.get("hq_api_key",       "")
OLLAMA_URL  = _cfg.get("ollama_hq_url",    "http://YOUR_HQ_TAILSCALE_IP:11434")
OLLAMA_FB   = _cfg.get("ollama_fallback_model", "mistral:latest")  # fallback when LM Studio offline
TIMEOUT     = 120  # LM Studio can be slow on first token

# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lm_status",
            "description": "Check if LM Studio server is running on simon-hq and which model is currently loaded. Use when asked 'LM Studio status', 'is LM Studio running', 'LM Studio check'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lm_ask",
            "description": "Send a prompt to the currently loaded model in LM Studio on simon-hq. Use when asked 'ask LM Studio', 'use LM Studio to', 'query the local model', 'what does LM Studio think'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt":      {"type": "string", "description": "The prompt or question to send"},
                    "temperature": {"type": "number", "description": "Response creativity 0.0–1.0 (default 0.7)"},
                    "max_tokens":  {"type": "integer", "description": "Max response length in tokens (default 500)"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lm_list_models",
            "description": "List all models available in LM Studio on simon-hq. Use when asked 'what models does LM Studio have', 'list LM Studio models', 'what can LM Studio run'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lm_compare",
            "description": "Send the same prompt to both HQ Ollama and LM Studio simultaneously and compare their responses side by side. Use when asked 'compare HQ and LM Studio', 'what do both models think', 'compare models on this'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The question or prompt to compare both models on"}
                },
                "required": ["prompt"]
            }
        }
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _lm_get(path: str, timeout: int = 8) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(f"{LM_URL}{path}")
        r.raise_for_status()
        return r.json()


import re as _re

def _strip_thinking(text: str) -> str:
    """Strip DeepSeek R1 / Qwen3 <think>...</think> reasoning blocks.
    These models 'think out loud' before answering — we only want the final answer.
    Handles both closed tags and unclosed tags (mid-stream truncation)."""
    # Remove complete <think>...</think> blocks
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    # Remove any remaining unclosed <think> block (truncated response)
    text = _re.sub(r"<think>.*", "", text, flags=_re.DOTALL)
    return text.strip()

async def _lm_chat(prompt: str, temperature: float = 0.7, max_tokens: int = 500) -> str:
    """Send a chat completion request to LM Studio.
    Automatically strips DeepSeek R1 / Qwen3 <think> reasoning blocks."""
    payload = {
        "messages":   [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "stream":      False,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{LM_URL}/v1/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
    choices = data.get("choices", [])
    if not choices:
        return "LM Studio returned no response."
    content   = choices[0].get("message", {}).get("content", "").strip()
    model_used = data.get("model", "unknown")
    # Strip reasoning chains — DeepSeek R1 and Qwen3 think before answering
    content = _strip_thinking(content)
    if not content:
        return f"[{model_used}] (Model returned only a reasoning chain — no final answer. Try rephrasing.)"
    return f"[{model_used}] {content}"


async def _hq_chat(prompt: str) -> str:
    """Send a chat request to HQ Ollama for comparison."""
    try:
        hq_model = _cfg.get("hq_model", "qwen2.5:7b")
        payload = {
            "model":    hq_model,
            "messages": [{"role": "user", "content": prompt}],
            "api_key":  HQ_KEY,
        }
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{HQ_URL}/llm/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        msg = data.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        return f"[HQ {hq_model}] {content[:600]}"
    except Exception as e:
        return f"[HQ] Error: {e}"


async def _ollama_chat_fallback(prompt: str, temperature: float = 0.7, max_tokens: int = 500) -> str:
    """Fallback to Ollama on simon-hq when LM Studio is offline."""
    payload = {
        "model":    OLLAMA_FB,
        "messages": [{"role": "user", "content": prompt}],
        "stream":   False,
        "options":  {"temperature": temperature, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message", {})
    content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
    return f"[Ollama/{OLLAMA_FB} — LM Studio offline fallback] {content.strip()}"

def _not_running_msg() -> str:
    return (
        "LM Studio server is not running on simon-hq. "
        "To start it: open LM Studio → click the API/Developer tab → click Start Server. "
        "It will run on port 1234. Falling back to Ollama mistral:latest automatically."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

async def execute(name: str, args: dict) -> Optional[str]:

    # ── lm_status ────────────────────────────────────────────────────────────
    if name == "lm_status":
        try:
            data = await _lm_get("/v1/models", timeout=5)
            models = data.get("data", [])
            if not models:
                return "LM Studio server is running on simon-hq but no model is currently loaded. Load a model in the LM Studio UI first."
            loaded = [m.get("id", "?") for m in models]
            return (
                f"LM Studio is online at {LM_URL}. "
                f"{len(loaded)} model(s) available: {', '.join(loaded)}. "
                f"Ready to accept requests."
            )
        except httpx.ConnectError:
            return _not_running_msg()
        except Exception as e:
            return f"LM Studio status check error: {e}"

    # ── lm_ask ────────────────────────────────────────────────────────────────
    elif name == "lm_ask":
        prompt      = args.get("prompt", "").strip()
        temperature = float(args.get("temperature", 0.7))
        max_tokens  = int(args.get("max_tokens", 500))
        if not prompt:
            return "No prompt provided."
        try:
            response = await _lm_chat(prompt, temperature, max_tokens)
            return response[:700] if len(response) > 700 else response
        except (httpx.ConnectError, httpx.TimeoutException):
            # LM Studio offline — fall back to Ollama mistral:latest on simon-hq
            print("[LM Studio] Offline — falling back to Ollama mistral:latest")
            try:
                response = await _ollama_chat_fallback(prompt, temperature, max_tokens)
                return response[:700] if len(response) > 700 else response
            except Exception as fe:
                return f"LM Studio offline and Ollama fallback failed: {fe}"
        except Exception as e:
            return f"LM Studio error: {e}"

    # ── lm_list_models ───────────────────────────────────────────────────────
    elif name == "lm_list_models":
        try:
            data = await _lm_get("/v1/models", timeout=5)
            models = data.get("data", [])
            if not models:
                return "No models found in LM Studio. Load a model via the LM Studio UI first."
            names = [m.get("id", "?") for m in models]
            return f"LM Studio has {len(names)} model(s): {', '.join(names)}."
        except httpx.ConnectError:
            return _not_running_msg()
        except Exception as e:
            return f"Error listing LM Studio models: {e}"

    # ── lm_compare ───────────────────────────────────────────────────────────
    elif name == "lm_compare":
        prompt = args.get("prompt", "").strip()
        if not prompt:
            return "No prompt provided for comparison."
        try:
            # Run both requests concurrently
            lm_task  = asyncio.create_task(_lm_chat(prompt, temperature=0.7, max_tokens=300))
            hq_task  = asyncio.create_task(_hq_chat(prompt))
            lm_resp, hq_resp = await asyncio.gather(lm_task, hq_task, return_exceptions=True)
            if isinstance(lm_resp, Exception):
                lm_resp = f"LM Studio error: {lm_resp}"
            if isinstance(hq_resp, Exception):
                hq_resp = f"HQ error: {hq_resp}"
            return (
                f"Comparison on: '{prompt[:60]}...' \n\n"
                f"LM STUDIO: {str(lm_resp)[:400]}\n\n"
                f"HQ OLLAMA: {str(hq_resp)[:400]}"
            )
        except httpx.ConnectError:
            return _not_running_msg()
        except Exception as e:
            return f"Comparison error: {e}"

    return None
