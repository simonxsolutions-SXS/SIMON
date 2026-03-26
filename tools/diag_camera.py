#!/usr/bin/env python3
"""
diag_camera.py — Diagnoses the exact camera failure and reports what's blocking it.
Run: /opt/homebrew/bin/python3.11 ~/Projects/AI-Projects/jarvis/diag_camera.py
"""
import subprocess, os, sys
from pathlib import Path

print("\n  S.I.M.O.N. Camera Diagnostic\n")

# ── 1. Check SKIP_AUTH in all relevant files ──────────────────────────────────
print("── 1. SKIP_AUTH flag status ─────────────────────────────────────────")
files_to_check = [
    Path.home() / "Projects/AI-Projects/jarvis/jarvis.py",
    Path.home() / "Projects/AI-Projects/jarvis/vision/simon_vision.py",
]
for f in files_to_check:
    src = f.read_text()
    if "SKIP_AUTH'] = '1'" in src or 'SKIP_AUTH"] = "1"' in src:
        print(f"  ❌ {f.name}: STILL has SKIP_AUTH=1 — this must be removed")
    elif "SKIP_AUTH" in src:
        line = [l for l in src.splitlines() if "SKIP_AUTH" in l][0].strip()
        print(f"  ✅ {f.name}: {line}")
    else:
        print(f"  ✅ {f.name}: no SKIP_AUTH (clean)")

# ── 2. Check TCC camera permission ───────────────────────────────────────────
print("\n── 2. macOS TCC Camera Permission ──────────────────────────────────")
try:
    r = subprocess.run(
        ["sqlite3",
         "/Library/Application Support/com.apple.TCC/TCC.db",
         "SELECT client, allowed, auth_reason FROM access WHERE service='kTCCServiceCamera';"],
        capture_output=True, text=True
    )
    if r.stdout.strip():
        for line in r.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 2:
                client  = parts[0]
                allowed = "✅ GRANTED" if parts[1] == "1" else "❌ DENIED"
                print(f"  {allowed} : {client}")
    else:
        print("  ⚠️  No camera entries in TCC — permission was never requested")
        print("  This means SKIP_AUTH=1 was set when OpenCV ran — permission dialog never appeared")
except Exception as e:
    print(f"  TCC check failed (needs sudo): {e}")
    print("  Run manually: sudo sqlite3 '/Library/Application Support/com.apple.TCC/TCC.db'")
    print("  'SELECT client,allowed FROM access WHERE service=\"kTCCServiceCamera\";'")

# ── 3. Try opening camera right now ──────────────────────────────────────────
print("\n── 3. Live camera test ──────────────────────────────────────────────")
# Make sure SKIP_AUTH is NOT set
os.environ.pop("OPENCV_AVFOUNDATION_SKIP_AUTH", None)

try:
    import cv2
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret and frame is not None:
            brightness = frame.mean()
            print(f"  ✅ Camera opened and returning frames (brightness={brightness:.1f})")
        else:
            print(f"  ⚠️  Camera opened but no frame returned (may need permission)")
        cap.release()
    else:
        print("  ❌ Camera failed to open")
        print("  This confirms camera permission is NOT granted to this Python process")
except Exception as e:
    print(f"  ❌ Camera test error: {e}")

# ── 4. Check which process holds the camera permission ───────────────────────
print("\n── 4. Process identity ──────────────────────────────────────────────")
print(f"  Python binary : {sys.executable}")
print(f"  Process name  : {Path(sys.executable).name}")
print(f"  PID           : {os.getpid()}")

# The TCC permission is granted to the binary, not the script.
# When launchd runs jarvis.py, it uses /opt/homebrew/bin/python3.11
# That binary needs the camera permission — not Terminal itself.
python_bin = sys.executable
print(f"\n  The permission must be granted to: {python_bin}")
print(f"  In System Settings → Privacy → Camera, look for that exact binary.")

# ── 5. Reset TCC to force re-prompt ──────────────────────────────────────────
print("\n── 5. How to fix if permission is missing ───────────────────────────")
print("""
  Option A (recommended): Reset and re-prompt
    tccutil reset Camera
    Then run this script again — macOS will show the permission dialog.
    Click ALLOW.

  Option B: Check System Settings directly
    System Settings → Privacy & Security → Camera
    Look for Terminal AND python3.11 — both should be ON.
    If python3.11 is not listed, run option A first.

  Option C: Grant via tccutil (if you know the bundle ID)
    The python3.11 binary at /opt/homebrew/bin/python3.11 is what needs access.
""")

print("  Diagnostic complete.\n")
