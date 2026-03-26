#!/usr/bin/env python3
"""
test_hq_vision.py — Tests the complete vision pipeline end to end.
Captures a frame from the Mac camera, sends it to HQ, prints the answer.
Run BEFORE asking SIMON to confirm the pipeline works independently.

Usage:
  /opt/homebrew/bin/python3.11 ~/Projects/AI-Projects/jarvis/test_hq_vision.py
"""
import asyncio, json, base64, time, sys
from pathlib import Path

cfg      = json.loads((Path(__file__).parent / "config.json").read_text())
HQ_URL   = cfg.get("hq_api_url", "http://YOUR_HQ_TAILSCALE_IP:8200")
HQ_KEY   = cfg.get("hq_api_key", "")

async def main():
    import httpx

    print("\n  S.I.M.O.N. HQ Vision Pipeline Test\n")

    # ── Step 1: Camera frame ─────────────────────────────────
    print("── Step 1: Capture frame from Mac camera ───────────────────────")
    import os
    os.environ.pop("OPENCV_AVFOUNDATION_SKIP_AUTH", None)
    import cv2

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("  ❌ Camera failed to open — check System Settings → Privacy → Camera → Terminal")
        return

    # Warm up camera (drain black frames)
    frame = None
    deadline = time.time() + 8
    while time.time() < deadline:
        ret, f = cap.read()
        if ret and f is not None and f.mean() > 2.0:
            frame = f
            break
        time.sleep(0.05)
    cap.release()

    if frame is None:
        print("  ❌ No usable frame from camera")
        return

    brightness = frame.mean()
    print(f"  ✅ Frame captured — brightness={brightness:.1f}, size={frame.shape[1]}x{frame.shape[0]}")

    # Encode to base64
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    img_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
    print(f"  ✅ Encoded to base64 — {len(img_b64):,} chars")

    # ── Step 2: Send to HQ ────────────────────────────────────
    print("\n── Step 2: Send to HQ llama3.2-vision:11b ──────────────────────")
    question = "How many fingers am I holding up? Look carefully and give just the number."
    print(f"  Question: {question}")
    print(f"  Sending to: {HQ_URL}/vision/ask")
    print(f"  (first call loads the model — may take 30-60s)")

    t0 = time.time()
    async with httpx.AsyncClient(timeout=120) as c:
        try:
            r = await c.post(f"{HQ_URL}/vision/ask", json={
                "image_b64": img_b64,
                "question":  question,
                "api_key":   HQ_KEY,
            })
            ms = round((time.time() - t0) * 1000)
            print(f"  HTTP status : {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"  ✅ Answer ({ms}ms): {data.get('answer', 'no answer')}")
                print(f"  Model used : {data.get('model', '?')}")
            else:
                print(f"  ❌ Error: {r.text[:300]}")
        except Exception as e:
            print(f"  ❌ Request failed: {e}")

    # ── Step 3: Check _hq_online flag logic ──────────────────
    print("\n── Step 3: Check HQ health endpoint ────────────────────────────")
    async with httpx.AsyncClient(timeout=5) as c:
        try:
            r = await c.get(f"{HQ_URL}/health")
            d = r.json()
            print(f"  status      : {d.get('status')}")
            print(f"  ready       : {d.get('ready')}")
            print(f"  models_warm : {d.get('models_warm')}")
            print(f"  vision_model: {d.get('vision_model')}")
            print(f"\n  ✅ HQ health OK — _hq_online will be True in SIMON after restart")
        except Exception as e:
            print(f"  ❌ Health check failed: {e}")
            print(f"  ❌ _hq_online will stay False — vision will fall back to Moondream")

    print("\n  ─────────────────────────────────────────────────────────────")
    print("  If Step 2 returned a correct answer, the pipeline works.")
    print("  Restart SIMON and it will use HQ vision automatically:")
    print("  launchctl kickstart -k gui/$(id -u)/com.simonx.simon")
    print()

asyncio.run(main())
