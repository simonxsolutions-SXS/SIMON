#!/usr/bin/env python3
"""
S.I.M.O.N. MLX Local Fast Path — simon_mlx.py
Clean version — fixes 'temp' → 'temperature' API change and adds
graceful fallback if mlx-lm API changes again.
"""

import asyncio
import threading
import time
from typing import Optional

_model     = None
_tokenizer = None
_lock      = threading.Lock()
_ready     = False
_load_error: Optional[str] = None

MODEL_ID = "mlx-community/Mistral-7B-Instruct-v0.3-4bit"


def load_model(verbose: bool = True) -> bool:
    global _model, _tokenizer, _ready, _load_error
    with _lock:
        if _ready:
            return True
        if _load_error:
            return False
        try:
            if verbose:
                print(f"[MLX] Loading {MODEL_ID}...")
            t0 = time.time()
            from mlx_lm import load
            _model, _tokenizer = load(MODEL_ID)
            elapsed = time.time() - t0
            if verbose:
                print(f"[MLX] ✅ Mistral 7B ready in {elapsed:.1f}s — fast path active")
            _ready = True
            return True
        except Exception as e:
            _load_error = str(e)
            print(f"[MLX] ❌ Load failed: {e}")
            _ready = False
            return False


def is_ready() -> bool:
    return _ready


# ─── Intent classifier ───────────────────────────────────────────────────────

_FAST_KEYWORDS = {
    "what time", "what day", "good morning", "good evening", "good afternoon",
    "hey simon", "hello simon", "hi simon", "system check", "system status",
    "cpu", "ram", "disk", "how's the machine", "run diagnostics",
    "calendar today", "what's on my calendar", "upcoming events",
    "any meetings", "check my messages", "any texts", "recent messages",
    "set a reminder", "create a reminder", "remind me", "check reminders",
    "what do you remember", "recall", "remember that",
    "ping", "speed test", "what's my ip", "public ip", "dns lookup",
    "wifi", "network", "weather", "temperature", "forecast",
    "what do you see", "what's on my desk", "who's there", "is anyone",
    "read that", "ocr", "list files", "search contacts", "find contact",
}

_DEEP_KEYWORDS = {
    "write an email", "draft an email", "compose an email",
    "write a message", "help me write", "draft a",
    "explain", "analyze", "analysis", "summarize",
    "what do you think about", "give me your thoughts",
    "plan", "strategy", "recommend", "should i",
    "create a report", "compare", "pros and cons", "research",
}

_DEEP_STARTERS = (
    "write", "draft", "compose", "explain in detail", "analyze",
    "create a", "help me", "what do you think", "should i",
)


def classify_intent(text: str) -> str:
    t = text.lower().strip()
    word_count = len(t.split())
    if word_count <= 3:
        return "fast"
    for kw in _DEEP_KEYWORDS:
        if kw in t:
            return "deep"
    for starter in _DEEP_STARTERS:
        if t.startswith(starter):
            return "deep"
    if word_count > 25 and not any(kw in t for kw in _FAST_KEYWORDS):
        return "deep"
    return "fast"


# ─── Inference ───────────────────────────────────────────────────────────────

def _build_prompt(user_message: str, recent_history: list) -> str:
    parts = []
    relevant = [m for m in recent_history if m["role"] in ("user", "assistant")][-8:]
    for msg in relevant:
        if msg["role"] == "user":
            parts.append(f"[INST] {msg['content'][:300]} [/INST]")
        else:
            parts.append(msg["content"][:300])
    parts.append(f"[INST] {user_message} [/INST]")
    return "".join(parts)


async def generate_fast(
    user_message: str,
    history: list,
    max_tokens: int = 120,
    temperature: float = 0.3,
) -> str:
    if not _ready:
        raise RuntimeError("MLX model not loaded")

    prompt = _build_prompt(user_message, history)
    loop = asyncio.get_event_loop()

    def _infer():
        from mlx_lm import generate
        import inspect
        sig = inspect.signature(generate)
        params = sig.parameters

        # Handle both old API (temp=) and new API (temperature=)
        kwargs = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "verbose": False,
        }
        if "temperature" in params:
            kwargs["temperature"] = temperature
        elif "temp" in params:
            kwargs["temp"] = temperature

        response = generate(_model, _tokenizer, **kwargs)
        return response.strip().replace("</s>", "").strip()

    result = await loop.run_in_executor(None, _infer)
    return result


def status() -> dict:
    return {
        "ready":    _ready,
        "model_id": MODEL_ID,
        "error":    _load_error,
        "device":   "mps (Apple Silicon GPU)",
    }
