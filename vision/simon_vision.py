#!/usr/bin/env python3
"""
S.I.M.O.N. Vision Engine — simon_vision.py
100% LOCAL — nothing leaves the Mac.

Architecture:
  Camera (12MP 1080p) → frame buffer
    ├── YOLO26n (MPS)       → object detection — 30+ FPS real-time
    ├── Moondream2 (MPS)    → scene Q&A, captions, OCR, VQA — on demand
    ├── DeepFace/ArcFace    → face recognition — registered faces only
    └── Presence detector   → is someone at the desk? motion?

All results persist to simon_kb.db (vision tables).
SIMON queries this engine via tool calls — no separate process needed.

Usage:
  from vision.simon_vision import VisionEngine
  engine = VisionEngine()
  result = engine.analyze_scene("What is on the desk?")
  result = engine.detect_objects()
  result = engine.identify_person()
  result = engine.capture_and_describe()
"""

import os
# IMPORTANT: Do NOT set OPENCV_AVFOUNDATION_SKIP_AUTH=1
# Setting it to 1 bypasses the macOS camera permission dialog entirely,
# causing PERMANENT silent failure. The camera never gets authorized.
# 
# macOS handles camera auth via TCC. Once Terminal has camera permission
# (System Settings → Privacy → Camera → Terminal = ON), OpenCV works fine
# from background processes including launchd.
#
# If camera fails: System Settings → Privacy & Security → Camera → enable Terminal
os.environ.pop('OPENCV_AVFOUNDATION_SKIP_AUTH', None)  # let macOS handle auth

import cv2
import time
import threading
import sqlite3
import base64
import io
import json
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
VISION_DIR  = Path(__file__).parent
FACES_DIR   = VISION_DIR / "faces"
KB_PATH     = Path.home() / ".simon-x" / "simon_kb.db"
YOLO_MODEL  = BASE_DIR / "yolo26n.pt"   # primary
YOLO_BACKUP = BASE_DIR / "yolo11n.pt"   # fallback if 26 not downloaded yet

FACES_DIR.mkdir(exist_ok=True)


# ─── DB Schema for Vision ─────────────────────────────────────────────────────
VISION_SCHEMA = """
-- Detection event log — what SIMON saw and when
CREATE TABLE IF NOT EXISTS vision_detections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,                    -- ISO timestamp
    source       TEXT DEFAULT 'webcam',            -- webcam | file | snapshot
    objects      TEXT,                             -- JSON: [{label, conf, bbox}]
    scene_desc   TEXT,                             -- Moondream caption
    faces_found  INTEGER DEFAULT 0,                -- count of faces detected
    face_names   TEXT,                             -- JSON: ["name1", "unknown"]
    raw_query    TEXT,                             -- the question asked
    raw_answer   TEXT,                             -- the answer returned
    ocr_text     TEXT                              -- any text found in image
);
CREATE INDEX IF NOT EXISTS idx_vis_ts    ON vision_detections(ts DESC);
CREATE INDEX IF NOT EXISTS idx_vis_src   ON vision_detections(source);

-- Registered faces — who SIMON knows
CREATE TABLE IF NOT EXISTS vision_faces (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    photo_path   TEXT NOT NULL,                    -- path to reference image
    embedding    BLOB,                             -- ArcFace embedding (optional cache)
    notes        TEXT,                             -- e.g. "owner", "family"
    registered   TEXT NOT NULL,
    last_seen    TEXT,
    seen_count   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_face_name ON vision_faces(name);

-- Presence log — when someone was detected at the camera
CREATE TABLE IF NOT EXISTS vision_presence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    person_name  TEXT DEFAULT 'unknown',
    confidence   REAL,
    duration_s   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pres_ts  ON vision_presence(ts DESC);
CREATE INDEX IF NOT EXISTS idx_pres_who ON vision_presence(person_name);
"""


def _get_db():
    """Get a connection to the SIMON KB — creates vision tables if needed."""
    KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(KB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(VISION_SCHEMA)
    conn.commit()
    return conn


def _log_detection(objects=None, scene_desc=None, faces_found=0,
                   face_names=None, raw_query=None, raw_answer=None,
                   ocr_text=None, source="webcam"):
    """Persist a vision detection event to the KB."""
    conn = _get_db()
    conn.execute("""
        INSERT INTO vision_detections
            (ts, source, objects, scene_desc, faces_found, face_names,
             raw_query, raw_answer, ocr_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(), source,
        json.dumps(objects) if objects else None,
        scene_desc,
        faces_found,
        json.dumps(face_names) if face_names else None,
        raw_query, raw_answer, ocr_text
    ))
    conn.commit()
    conn.close()


# ─── Vision Engine ─────────────────────────────────────────────────────────────
class VisionEngine:
    """
    S.I.M.O.N.'s visual cortex.
    Lazy-loads all models — nothing loads until first use.
    Thread-safe frame buffer for continuous webcam access.
    """

    def __init__(self):
        self._yolo          = None
        self._moondream     = None
        self._md_tokenizer  = None
        self._cap           = None          # cv2.VideoCapture
        self._frame         = None          # latest frame (BGR numpy)
        self._frame_lock    = threading.Lock()
        self._streaming     = False
        self._stream_thread = None
        self._device        = self._best_device()
        print(f"[Vision] Engine init — device: {self._device}")

    # ── Device ───────────────────────────────────────────────────────────────
    @staticmethod
    def _best_device() -> str:
        try:
            import torch
            if torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    # ── YOLO loader ──────────────────────────────────────────────────────────
    def _load_yolo(self):
        if self._yolo is not None:
            return True
        try:
            from ultralytics import YOLO
            # Prefer YOLO26n, fall back to YOLO11n
            model_path = str(YOLO_MODEL) if YOLO_MODEL.exists() else str(YOLO_BACKUP)
            if not Path(model_path).exists():
                # Let ultralytics download — will work from user's Terminal
                model_path = "yolo26n.pt"
            self._yolo = YOLO(model_path)
            print(f"[Vision] YOLO loaded: {model_path} | {len(self._yolo.names)} classes")
            return True
        except Exception as e:
            print(f"[Vision] YOLO load failed: {e}")
            return False

    # ── Moondream loader ─────────────────────────────────────────────────────
    def _load_moondream(self):
        if self._moondream is not None:
            return True

        # Strategy 1: moondream pip package (simplest, most reliable)
        try:
            import moondream as md
            import torch
            print("[Vision] Loading Moondream2 via pip package...")
            model_path = Path.home() / ".cache" / "moondream" / "moondream-2b-int8.mf"
            if model_path.exists():
                self._moondream = md.vl(model=str(model_path))
            else:
                self._moondream = md.vl()  # downloads on first use
            self._moondream_backend = "pip"
            print(f"[Vision] Moondream2 loaded via pip package")
            return True
        except Exception as e1:
            print(f"[Vision] moondream pip failed: {e1} — trying transformers...")

        # Strategy 2: transformers (original approach)
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch
            print("[Vision] Loading Moondream2 via transformers...")
            self._md_tokenizer = AutoTokenizer.from_pretrained(
                "vikhyatk/moondream2",
                revision="2025-06-21",
                trust_remote_code=True,
            )
            self._moondream = AutoModelForCausalLM.from_pretrained(
                "vikhyatk/moondream2",
                revision="2025-06-21",
                trust_remote_code=True,
                device_map={"": self._device},
                torch_dtype=torch.float16,
            )
            self._moondream.eval()
            self._moondream_backend = "transformers"
            print(f"[Vision] Moondream2 loaded via transformers on {self._device}")
            return True
        except Exception as e2:
            print(f"[Vision] Moondream transformers failed: {e2}")
            print("[Vision] To fix: pip3.11 install 'transformers>=4.36,<4.45' moondream --break-system-packages")
            return False

    # ── Camera ───────────────────────────────────────────────────────────────
    def _find_builtin_camera_index(self) -> int:
        """
        Scan camera indices 0-5 and return the index of the camera that
        actually produces real (non-black) frames.

        On some macOS configurations a virtual or ghost camera sits at
        index 0 and opens fine but only returns black frames (brightness
        near 0). The real built-in camera may be at index 1 or higher.

        Strategy: for each available camera, read up to 15 frames and
        check the mean brightness. The first camera that produces a frame
        with brightness > 2.0 is selected. Falls back to 0 if nothing
        produces real frames (e.g. room is completely dark).
        """
        best_idx        = 0    # fallback
        best_brightness = -1.0

        for idx in range(6):
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                cap.release()
                continue

            w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(cap.get(cv2.CAP_PROP_FPS))

            # Read up to 15 frames looking for non-black content
            peak = 0.0
            for _ in range(15):
                ret, frame = cap.read()
                if ret and frame is not None:
                    b = float(frame.mean())
                    if b > peak:
                        peak = b
                if peak > 2.0:
                    break
                time.sleep(0.04)
            cap.release()

            print(f"[Vision] Camera [{idx}]: {w}x{h} @ {fps}fps  peak_brightness={peak:.1f}")

            # First camera that actually produces real frames wins
            if peak > 2.0 and best_brightness < 2.0:
                best_idx        = idx
                best_brightness = peak
            elif peak > best_brightness:
                # Track best option even if nothing clears 2.0
                best_brightness = peak
                best_idx        = idx

        print(f"[Vision] Selected camera index: {best_idx} (brightness={best_brightness:.1f})")
        return best_idx

    def _open_camera(self) -> bool:
        if self._cap and self._cap.isOpened():
            return True

        idx = self._find_builtin_camera_index()
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            print("[Vision] Cannot open webcam — grant Camera access to Terminal in:"
                  " System Settings → Privacy & Security → Camera")
            return False

        # Request best quality from the 12MP Center Stage camera
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # minimize latency
        self._cap = cap
        self._cam_idx = idx
        print(f"[Vision] Camera [{idx}] opened: "
              f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
              f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ "
              f"{int(cap.get(cv2.CAP_PROP_FPS))}fps")
        return True

    def _release_camera(self):
        self._streaming = False
        if self._cap:
            self._cap.release()
            self._cap = None
        print("[Vision] Camera released")

    def _stream_loop(self):
        """Background thread: continuously reads frames into buffer.
        Skips the first black warm-up frames from macOS AVFoundation.
        """
        warmup_done = False
        while self._streaming and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if ret and frame is not None:
                # Skip black frames during camera warm-up
                if not warmup_done:
                    if frame.mean() > 5.0:
                        warmup_done = True
                    else:
                        time.sleep(0.03)
                        continue
                with self._frame_lock:
                    self._frame = frame
            time.sleep(0.01)   # ~100fps read rate (camera limits to 30)

    def start_stream(self) -> bool:
        """Start continuous background frame capture."""
        if not self._open_camera():
            return False
        self._streaming = True
        self._stream_thread = threading.Thread(
            target=self._stream_loop, daemon=True, name="simon-vision-stream"
        )
        self._stream_thread.start()
        time.sleep(0.5)   # let camera warm up
        print("[Vision] Stream started")
        return True

    def stop_stream(self):
        """Stop background capture and release camera."""
        self._release_camera()

    def grab_frame(self) -> Optional[np.ndarray]:
        """
        Get latest frame. Uses stream buffer if active, otherwise one-shot capture.
        Returns BGR numpy array or None.

        macOS camera warm-up: the built-in camera returns black frames for the
        first ~30 frames while the sensor auto-exposes. We drain until we get
        a non-black frame or hit a timeout.
        """
        # Try buffered stream first — stream already handles warm-up
        if self._streaming:
            with self._frame_lock:
                if self._frame is not None:
                    return self._frame.copy()

        # One-shot capture with warm-up
        if not self._open_camera():
            return None

        # macOS AVFoundation warm-up: some cameras need many frames to
        # auto-expose. Poll until brightness > 2.0 or 12s timeout.
        # (Index 0 ghost camera never brightens — index selection handles this.)
        deadline = time.time() + 12.0
        frame    = None
        last_f   = None
        while time.time() < deadline:
            ret, f = self._cap.read()
            if not ret or f is None:
                time.sleep(0.03)
                continue
            last_f = f
            if f.mean() > 2.0:     # real content
                frame = f
                break
            time.sleep(0.03)

        # If still dark, return whatever we have (room may just be dark)
        if frame is None and last_f is not None:
            print(f"[Vision] Warm-up timeout — returning last frame (brightness={last_f.mean():.1f})")
        return frame if frame is not None else last_f

    def frame_to_pil(self, frame: np.ndarray):
        """Convert BGR OpenCV frame to PIL Image for Moondream."""
        from PIL import Image
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def frame_to_base64(self, frame: np.ndarray) -> str:
        """Convert frame to base64 JPEG string."""
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(buf.tobytes()).decode("utf-8")

    def save_snapshot(self, frame: np.ndarray, label: str = "") -> str:
        """Save a snapshot to ~/.simon-x/snapshots/ and return path."""
        snap_dir = Path.home() / ".simon-x" / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts}_{label}.jpg" if label else f"{ts}.jpg"
        path = str(snap_dir / name)
        cv2.imwrite(path, frame)
        return path

    # ── YOLO26 Object Detection ───────────────────────────────────────────────
    def detect_objects(self, frame: Optional[np.ndarray] = None,
                       conf: float = 0.45, save: bool = False) -> dict:
        """
        Run YOLO26n on the current frame.

        Returns:
          {
            "objects": [{"label": "laptop", "conf": 0.92, "bbox": [x1,y1,x2,y2]}, ...],
            "summary": "laptop, coffee cup, person",
            "count":   3,
            "raw_frame_path": "/path/to/snap.jpg"  # only if save=True
          }
        """
        if not self._load_yolo():
            return {"error": "YOLO model not available — run: python3.11 -c \"from ultralytics import YOLO; YOLO('yolo26n.pt')\"",
                    "objects": [], "summary": "", "count": 0}

        if frame is None:
            frame = self.grab_frame()
        if frame is None:
            return {"error": "Cannot access webcam", "objects": [], "summary": "", "count": 0}

        t0 = time.time()
        results = self._yolo(frame, device=self._device, conf=conf, verbose=False)
        ms = (time.time() - t0) * 1000

        objects = []
        for r in results:
            for box in r.boxes:
                label = self._yolo.names[int(box.cls[0])]
                conf_val = float(box.conf[0])
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                objects.append({"label": label, "conf": round(conf_val, 3),
                                "bbox": [x1, y1, x2, y2]})

        # Sort by confidence
        objects.sort(key=lambda x: x["conf"], reverse=True)

        # De-duplicate labels for summary
        seen, summary_labels = set(), []
        for o in objects:
            if o["label"] not in seen:
                summary_labels.append(o["label"])
                seen.add(o["label"])
        summary = ", ".join(summary_labels) if summary_labels else "nothing detected"

        raw_path = self.save_snapshot(frame, "detect") if save else None

        _log_detection(objects=objects, source="webcam",
                       raw_query="detect_objects", raw_answer=summary)

        return {
            "objects": objects,
            "summary": summary,
            "count":   len(objects),
            "ms":      round(ms, 1),
            "raw_frame_path": raw_path,
        }

    # ── Moondream2 Scene Q&A ──────────────────────────────────────────────────
    def ask_scene(self, question: str, frame: Optional[np.ndarray] = None) -> dict:
        """
        Ask Moondream2 a natural language question about the current camera view.

        Examples:
          ask_scene("What is on the desk?")
          ask_scene("Is there anyone in the room?")
          ask_scene("What text can you read on the screen?")
          ask_scene("Describe the scene in one sentence.")
        """
        if not self._load_moondream():
            return {"error": "Moondream2 not available", "answer": ""}

        if frame is None:
            frame = self.grab_frame()
        if frame is None:
            return {"error": "Cannot access webcam", "answer": ""}

        t0  = time.time()
        img = self.frame_to_pil(frame)

        try:
            backend = getattr(self, "_moondream_backend", "transformers")
            if backend == "pip":
                # moondream pip package API
                result = self._moondream.query(img, question)
                answer = result.get("answer", str(result)) if isinstance(result, dict) else str(result)
            else:
                # transformers API
                result = self._moondream.query(img, question)
                answer = result["answer"] if isinstance(result, dict) else str(result)
        except Exception as e:
            return {"error": str(e), "answer": ""}

        ms = (time.time() - t0) * 1000

        _log_detection(scene_desc=answer, raw_query=question,
                       raw_answer=answer, source="webcam")

        return {
            "question": question,
            "answer":   answer.strip(),
            "ms":       round(ms, 1),
        }

    def caption_scene(self, frame: Optional[np.ndarray] = None,
                      length: str = "normal") -> dict:
        """
        Generate a natural language caption of the current camera view.
        length: "short" (one phrase) | "normal" (1-2 sentences)
        """
        if not self._load_moondream():
            return {"error": "Moondream2 not available", "caption": ""}

        if frame is None:
            frame = self.grab_frame()
        if frame is None:
            return {"error": "Cannot access webcam", "caption": ""}

        t0  = time.time()
        img = self.frame_to_pil(frame)

        try:
            result  = self._moondream.caption(img, length=length)
            caption = result["caption"] if isinstance(result, dict) else str(result)
            # Handle generator (streaming mode returns generator)
            if hasattr(caption, "__iter__") and not isinstance(caption, str):
                caption = "".join(caption)
        except Exception as e:
            return {"error": str(e), "caption": ""}

        ms = (time.time() - t0) * 1000

        _log_detection(scene_desc=caption, raw_query="caption",
                       raw_answer=caption, source="webcam")

        return {"caption": caption.strip(), "ms": round(ms, 1)}

    def read_text_in_scene(self, frame: Optional[np.ndarray] = None) -> dict:
        """
        Ask Moondream2 to read any visible text (OCR).
        Good for: whiteboards, screens, documents, sticky notes.
        """
        result = self.ask_scene(
            "Read all visible text in this image exactly as written. "
            "If there is no text, say 'no text visible'.",
            frame=frame
        )
        if "answer" in result:
            _log_detection(ocr_text=result["answer"], source="webcam",
                           raw_query="ocr", raw_answer=result["answer"])
        return result

    def detect_by_name(self, object_name: str,
                       frame: Optional[np.ndarray] = None) -> dict:
        """
        Use Moondream2's natural-language detect() to find a specific thing.
        More flexible than YOLO — can find anything by description.

        Example: detect_by_name("coffee mug")
                 detect_by_name("person wearing glasses")
        """
        if not self._load_moondream():
            return {"error": "Moondream2 not available", "found": False, "count": 0}

        if frame is None:
            frame = self.grab_frame()
        if frame is None:
            return {"error": "Cannot access webcam", "found": False, "count": 0}

        img = self.frame_to_pil(frame)

        try:
            result  = self._moondream.detect(img, object_name)
            objects = result.get("objects", []) if isinstance(result, dict) else []
        except Exception as e:
            return {"error": str(e), "found": False, "count": 0}

        return {
            "query":   object_name,
            "found":   len(objects) > 0,
            "count":   len(objects),
            "objects": objects,
        }

    # ── Face Recognition ─────────────────────────────────────────────────────
    def register_face(self, name: str, image_path: Optional[str] = None,
                      notes: str = "owner") -> dict:
        """
        Register a person's face so SIMON can recognize them.

        If image_path is None, takes a snapshot from the webcam.
        Stores reference image to vision/faces/{name}.jpg

        Usage:
          engine.register_face("Alex")           # captures from webcam
          engine.register_face("Sarah", "/path/to/photo.jpg")
        """
        if image_path is None:
            frame = self.grab_frame()
            if frame is None:
                return {"error": "Cannot access webcam", "success": False}
            face_path = str(FACES_DIR / f"{name.lower().replace(' ', '_')}.jpg")
            cv2.imwrite(face_path, frame)
            print(f"[Vision] Captured face photo for {name} → {face_path}")
        else:
            face_path = image_path

        if not Path(face_path).exists():
            return {"error": f"Image not found: {face_path}", "success": False}

        # Verify a face is actually detectable in the image
        try:
            from deepface import DeepFace
            objs = DeepFace.extract_faces(
                img_path=face_path,
                detector_backend="retinaface",
                enforce_detection=False,
            )
            face_detected = any(o.get("confidence", 0) > 0.7 for o in objs)
        except Exception as e:
            face_detected = False
            print(f"[Vision] Face verification warning: {e}")

        # Store in KB
        conn = _get_db()
        conn.execute("""
            INSERT INTO vision_faces (name, photo_path, notes, registered)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                photo_path=excluded.photo_path,
                notes=excluded.notes,
                registered=excluded.registered
        """, (name, face_path, notes, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        return {
            "success":        True,
            "name":           name,
            "photo_path":     face_path,
            "face_detected":  face_detected,
        }

    def identify_person(self, frame: Optional[np.ndarray] = None) -> dict:
        """
        Look at the camera and identify who is there using DeepFace ArcFace.

        Returns the best match from registered faces, or "unknown".
        Uses cosine distance — threshold 0.40 (ArcFace standard).
        """
        # Get registered faces from KB
        conn = _get_db()
        rows = conn.execute(
            "SELECT name, photo_path FROM vision_faces ORDER BY name"
        ).fetchall()
        conn.close()

        if not rows:
            return {
                "name":       "unknown",
                "confidence": 0.0,
                "message":    "No faces registered. Use register_face() first.",
            }

        if frame is None:
            frame = self.grab_frame()
        if frame is None:
            return {"error": "Cannot access webcam", "name": "unknown"}

        # Save temp frame for deepface
        tmp_path = "/tmp/simon_vision_id.jpg"
        cv2.imwrite(tmp_path, frame)

        try:
            from deepface import DeepFace

            best_match    = "unknown"
            best_distance = 999.0

            for row in rows:
                try:
                    result = DeepFace.verify(
                        img1_path        = tmp_path,
                        img2_path        = row["photo_path"],
                        model_name       = "ArcFace",
                        detector_backend = "retinaface",
                        enforce_detection= False,
                        silent           = True,
                    )
                    dist = result.get("distance", 999)
                    if result.get("verified") and dist < best_distance:
                        best_distance = dist
                        best_match    = row["name"]
                except Exception:
                    continue

            confidence = max(0.0, round(1.0 - (best_distance / 0.68), 3))

            # Log presence
            if best_match != "unknown":
                conn = _get_db()
                conn.execute("""
                    UPDATE vision_faces SET last_seen=?, seen_count=seen_count+1
                    WHERE name=?
                """, (datetime.now().isoformat(), best_match))
                conn.execute("""
                    INSERT INTO vision_presence (ts, person_name, confidence)
                    VALUES (?, ?, ?)
                """, (datetime.now().isoformat(), best_match, confidence))
                conn.commit()
                conn.close()

            _log_detection(faces_found=1,
                           face_names=[best_match],
                           raw_query="identify_person",
                           raw_answer=best_match)

            return {
                "name":       best_match,
                "confidence": confidence,
                "distance":   round(best_distance, 4),
                "matched":    best_match != "unknown",
            }

        except Exception as e:
            return {"error": str(e), "name": "unknown", "confidence": 0.0}

    def detect_presence(self, frame: Optional[np.ndarray] = None) -> dict:
        """
        Fast check: is anyone visible in the camera view?
        Uses YOLO person class — sub-100ms.
        """
        result = self.detect_objects(frame=frame, conf=0.50)
        people = [o for o in result.get("objects", []) if o["label"] == "person"]
        return {
            "person_present": len(people) > 0,
            "count": len(people),
            "confidence": max((p["conf"] for p in people), default=0.0),
        }

    # ── Full Analysis (SIMON's primary tool) ──────────────────────────────────
    def full_analysis(self, question: Optional[str] = None) -> dict:
        """
        Comprehensive scene analysis — runs all engines and returns
        a unified result SIMON can speak directly.

        Runs in order:
          1. YOLO26 object detection (fast, always)
          2. Presence check (from YOLO results)
          3. Moondream Q&A if question given, else caption
          4. Face ID if person detected

        This is what gets called when user says:
          "Simon, what do you see?"
          "Simon, is anyone there?"
          "Simon, describe my desk"
        """
        frame = self.grab_frame()
        if frame is None:
            return {"error": "Cannot access webcam — check System Preferences → Privacy → Camera",
                    "spoken": "I can't access the camera right now."}

        result = {
            "ts":           datetime.now().isoformat(),
            "objects":      [],
            "object_summary": "",
            "person_present": False,
            "face_name":    None,
            "scene_text":   "",
            "spoken":       "",
        }

        # 1. YOLO detection
        det = self.detect_objects(frame=frame)
        result["objects"]        = det.get("objects", [])
        result["object_summary"] = det.get("summary", "")
        result["detect_ms"]      = det.get("ms", 0)

        # 2. Presence
        people = [o for o in result["objects"] if o["label"] == "person"]
        result["person_present"] = len(people) > 0
        result["person_count"]   = len(people)

        # 3. Moondream — answer question or caption
        if question:
            qa = self.ask_scene(question, frame=frame)
            result["scene_text"] = qa.get("answer", "")
            result["question"]   = question
        else:
            cap = self.caption_scene(frame=frame, length="normal")
            result["scene_text"] = cap.get("caption", "")

        # 4. Face ID if person detected
        if result["person_present"]:
            face = self.identify_person(frame=frame)
            result["face_name"]       = face.get("name", "unknown")
            result["face_confidence"] = face.get("confidence", 0.0)

        # Build spoken summary
        parts = []
        if result["scene_text"]:
            parts.append(result["scene_text"])
        if result["object_summary"] and result["object_summary"] != "nothing detected":
            obj_note = f"Objects detected: {result['object_summary']}."
            if obj_note not in " ".join(parts):
                parts.append(obj_note)
        if result["face_name"] and result["face_name"] != "unknown":
            parts.append(f"I can see {result['face_name']}.")

        result["spoken"] = " ".join(parts).strip() or "Nothing notable in view."

        return result

    # ── Utility ───────────────────────────────────────────────────────────────
    def get_stats(self) -> dict:
        """Return vision system stats from the KB."""
        conn = _get_db()
        detections = conn.execute("SELECT COUNT(*) FROM vision_detections").fetchone()[0]
        faces      = conn.execute("SELECT COUNT(*) FROM vision_faces").fetchone()[0]
        presences  = conn.execute("SELECT COUNT(*) FROM vision_presence").fetchone()[0]
        last_det   = conn.execute(
            "SELECT ts, scene_desc FROM vision_detections ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        conn.close()

        return {
            "detections_logged": detections,
            "registered_faces":  faces,
            "presence_events":   presences,
            "last_detection":    dict(last_det) if last_det else None,
            "yolo_ready":        self._yolo is not None,
            "moondream_ready":   self._moondream is not None,
            "streaming":         self._streaming,
            "device":            self._device,
        }

    def get_recent_detections(self, limit: int = 10) -> list:
        """Pull recent detection log from KB."""
        conn = _get_db()
        rows = conn.execute("""
            SELECT ts, objects, scene_desc, face_names, raw_query, raw_answer
            FROM vision_detections
            ORDER BY ts DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def list_registered_faces(self) -> list:
        """Return all registered faces from KB."""
        conn = _get_db()
        rows = conn.execute(
            "SELECT name, notes, registered, last_seen, seen_count FROM vision_faces ORDER BY name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def who_was_seen_today(self) -> list:
        """Return presence log for today from KB."""
        today = datetime.now().strftime("%Y-%m-%d")
        conn  = _get_db()
        rows  = conn.execute("""
            SELECT person_name, COUNT(*) as times, MAX(ts) as last_seen
            FROM vision_presence
            WHERE ts LIKE ?
            GROUP BY person_name
            ORDER BY times DESC
        """, (f"{today}%",)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ─── Module-level singleton ────────────────────────────────────────────────────
_engine: Optional[VisionEngine] = None

def get_engine() -> VisionEngine:
    """Get the shared VisionEngine singleton."""
    global _engine
    if _engine is None:
        _engine = VisionEngine()
    return _engine


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    engine = VisionEngine()

    if cmd == "detect":
        print("Running YOLO26 object detection...")
        r = engine.detect_objects()
        print(f"  Found {r['count']} objects in {r.get('ms', '?')}ms")
        for o in r["objects"]:
            print(f"  {o['label']:20} {o['conf']:.0%}  bbox={o['bbox']}")
        print(f"  Summary: {r['summary']}")

    elif cmd == "describe":
        print("Capturing and describing scene with Moondream2...")
        r = engine.caption_scene()
        print(f"  Caption ({r.get('ms', '?')}ms):\n  {r.get('caption', r.get('error'))}")

    elif cmd == "ask":
        q = " ".join(sys.argv[2:]) or "What do you see?"
        print(f"Asking Moondream2: '{q}'")
        r = engine.ask_scene(q)
        print(f"  Answer ({r.get('ms', '?')}ms):\n  {r.get('answer', r.get('error'))}")

    elif cmd == "ocr":
        print("Reading text from camera view...")
        r = engine.read_text_in_scene()
        print(f"  Text found:\n  {r.get('answer', r.get('error'))}")

    elif cmd == "register":
        name = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("Name: ")
        print(f"Registering face for '{name}' from webcam in 3 seconds...")
        time.sleep(3)
        r = engine.register_face(name)
        print(f"  {'✅ Registered' if r['success'] else '❌ Failed'}: {r}")

    elif cmd == "identify":
        print("Looking for a known face...")
        r = engine.identify_person()
        print(f"  Person: {r['name']} (confidence: {r.get('confidence', 0):.0%})")

    elif cmd == "presence":
        r = engine.detect_presence()
        print(f"  Person present: {r['person_present']} ({r['count']} detected)")

    elif cmd == "full":
        q = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None
        print("Running full analysis...")
        r = engine.full_analysis(question=q)
        print(f"\n  SPOKEN RESPONSE:\n  {r['spoken']}")
        print(f"\n  Objects: {r['object_summary']}")
        print(f"  Person:  {r.get('face_name', 'none')}")

    elif cmd == "faces":
        faces = engine.list_registered_faces()
        if not faces:
            print("  No faces registered. Run: python3.11 simon_vision.py register [Name]")
        for f in faces:
            print(f"  {f['name']:20} seen {f['seen_count']}x  last: {f.get('last_seen','never')}")

    elif cmd == "history":
        rows = engine.get_recent_detections(limit=10)
        for r in rows:
            ts = r["ts"][:19]
            desc = r.get("scene_desc") or r.get("raw_answer") or ""
            print(f"  [{ts}] {desc[:80]}")

    elif cmd == "status":
        s = engine.get_stats()
        print(f"\n  S.I.M.O.N. Vision Engine Status")
        print(f"  Device:        {s['device']}")
        print(f"  YOLO ready:    {s['yolo_ready']}")
        print(f"  Moondream:     {s['moondream_ready']}")
        print(f"  Detections:    {s['detections_logged']} logged")
        print(f"  Faces:         {s['registered_faces']} registered")
        print(f"  Presences:     {s['presence_events']} events")

    else:
        print("Commands: detect | describe | ask [question] | ocr | register [name]")
        print("          identify | presence | full [question] | faces | history | status")
