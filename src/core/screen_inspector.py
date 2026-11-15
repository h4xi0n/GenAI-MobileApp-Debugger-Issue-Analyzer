"""Screen Inspector, Frame Compression, and Ring Buffer.

Handles device screenshot processing, Lanczos downsampling, WebP/JPEG compression,
base64 encoding, and thread-safe rolling ring buffer storage.

Author: Arun Krishnan
"""

import base64
import io
import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Literal

from PIL import Image

from .logger import setup_logger
from .models import ScreenFrame, ScreenState

logger = setup_logger("screen_inspector")


class ScreenInspector:
    """Processes, resizes, and encodes device screen frames for efficient multimodal transmission."""

    def __init__(self, max_dimension: int = 1024, default_format: Literal["image/webp", "image/jpeg"] = "image/webp", quality: int = 80):
        """Initializes ScreenInspector with dimension and compression parameters.

        Args:
            max_dimension: Maximum width or height constraint in pixels for downsampling.
            default_format: Default image MIME type ('image/webp' or 'image/jpeg').
            quality: Compression quality factor (1-100).
        """
        self.max_dimension = max_dimension
        self.default_format = default_format
        self.quality = quality

    def process_image_bytes(
        self,
        raw_bytes: bytes,
        mime_type: Literal["image/webp", "image/jpeg", "image/png"] | None = None,
        screen_state: ScreenState = ScreenState.NORMAL,
        frame_id: str | None = None,
    ) -> ScreenFrame:
        """Decodes raw image bytes, downsamples if exceeding max_dimension, and compresses to base64.

        Args:
            raw_bytes: Raw PNG, JPEG, or WebP bytes.
            mime_type: Target output format ('image/webp' or 'image/jpeg').
            screen_state: Visual anomaly state.
            frame_id: Optional custom frame identifier.

        Returns:
            ScreenFrame domain model.
        """
        target_mime = mime_type or self.default_format
        pil_format = "WEBP" if target_mime == "image/webp" else "JPEG"

        image = Image.open(io.BytesIO(raw_bytes))
        orig_w, orig_h = image.size

        # Convert RGBA/P to RGB if saving as JPEG
        if pil_format == "JPEG" and image.mode in ("RGBA", "P") or image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        # Downscale if needed maintaining aspect ratio with Lanczos
        new_w, new_h = orig_w, orig_h
        if max(orig_w, orig_h) > self.max_dimension:
            if orig_w >= orig_h:
                new_w = self.max_dimension
                new_h = int((orig_h * self.max_dimension) / orig_w)
            else:
                new_h = self.max_dimension
                new_w = int((orig_w * self.max_dimension) / orig_h)
            image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

        out_buffer = io.BytesIO()
        image.save(out_buffer, format=pil_format, quality=self.quality)
        compressed_bytes = out_buffer.getvalue()
        b64_str = base64.b64encode(compressed_bytes).decode("ascii")

        return ScreenFrame(
            frame_id=frame_id or str(uuid.uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            image_bytes_base64=b64_str,
            mime_type=target_mime,
            width=new_w,
            height=new_h,
            screen_state=screen_state,
        )

    def process_image_file(self, file_path: str, screen_state: ScreenState = ScreenState.NORMAL) -> ScreenFrame:
        """Reads and processes an image file from disk."""
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        return self.process_image_bytes(raw_bytes, screen_state=screen_state)


class FrameRingBuffer:
    """Thread-safe rolling ring buffer for storing recent ScreenFrames."""

    def __init__(self, capacity: int = 5):
        """Initializes a thread-safe fixed-capacity rolling ring buffer.

        Args:
            capacity: Maximum number of recent frames to retain before evicting oldest.
        """
        self.capacity = capacity
        self._buffer: deque[ScreenFrame] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def append(self, frame: ScreenFrame):
        """Adds a new frame to the ring buffer, evicting the oldest if full."""
        with self._lock:
            self._buffer.append(frame)

    def get_latest(self) -> ScreenFrame | None:
        """Returns the most recently appended frame."""
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer[-1]

    def get_all(self) -> list[ScreenFrame]:
        """Returns a snapshot copy of all frames in chronological order."""
        with self._lock:
            return list(self._buffer)

    def get_count(self) -> int:
        """Returns number of frames in buffer."""
        with self._lock:
            return len(self._buffer)

    def clear(self):
        """Clears all frames from the buffer."""
        with self._lock:
            self._buffer.clear()

    def snapshot_for_incident(self) -> list[ScreenFrame]:
        """Extracts the buffer content as an incident snapshot."""
        with self._lock:
            return list(self._buffer)
