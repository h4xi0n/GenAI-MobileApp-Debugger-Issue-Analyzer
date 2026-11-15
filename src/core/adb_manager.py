"""ADB Manager and Subprocess Engine.

Provides asynchronous and synchronous orchestration of Android devices via ADB,
with comprehensive mock mode support for offline execution.

Author: Arun Krishnan
"""

import asyncio
import io
import shutil
import subprocess
from collections.abc import AsyncGenerator

from PIL import Image, ImageDraw

from .logger import setup_logger
from .models import DeviceStatus

logger = setup_logger("adb_manager")


class ADBManager:
    """Manages ADB communication, device discovery, logcat streaming, and lifecycle actions."""

    def __init__(self, serial: str | None = None, mock_mode: bool | None = None):
        """Initialize ADBManager.

        Args:
            serial: Specific device serial to target. If None, targets the first available device.
            mock_mode: Force mock mode if True. If None, auto-detects based on adb binary availability.
        """
        self.adb_bin = shutil.which("adb")
        if mock_mode is not None:
            self.mock_mode = mock_mode
        else:
            self.mock_mode = self.adb_bin is None

        self.serial = serial
        self._active_logcat_proc: asyncio.subprocess.Process | None = None

    def _base_cmd(self) -> list[str]:
        """Constructs the base ADB command prefix with optional target device serial."""
        cmd = [self.adb_bin or "adb"]
        if self.serial:
            cmd.extend(["-s", self.serial])
        return cmd

    def check_connection(self) -> bool:
        """Returns True if adb binary is available and at least one device is online."""
        if self.mock_mode:
            return True
        try:
            res = subprocess.run([self.adb_bin, "devices"], capture_output=True, text=True, timeout=5)
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            devices = [line for line in lines[1:] if "\tdevice" in line]
            return len(devices) > 0
        except Exception as e:
            logger.warning(f"ADB connection check failed: {e}")
            return False

    def list_devices(self) -> list[DeviceStatus]:
        """Discovers all connected Android physical devices and emulators."""
        if self.mock_mode:
            return [
                DeviceStatus(
                    serial="emulator-5554",
                    model="Pixel_8_Pro_Virtual",
                    state="device",
                    battery_level=92,
                    is_streaming=False,
                    active_package="com.example.mobileapp"
                )
            ]

        try:
            res = subprocess.run([self.adb_bin, "devices", "-l"], capture_output=True, text=True, timeout=5)
            devices = []
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("List of devices"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    state = parts[1]
                    model = "Unknown"
                    for part in parts[2:]:
                        if part.startswith("model:"):
                            model = part.split(":", 1)[1]
                    devices.append(
                        DeviceStatus(
                            serial=serial,
                            model=model,
                            state=state,
                            is_streaming=False
                        )
                    )
            return devices
        except Exception as e:
            logger.error(f"Failed to list ADB devices: {e}")
            return []

    def capture_screenshot(self) -> bytes:
        """Captures a PNG screenshot via adb exec-out screencap -p or generates synthetic frame in mock mode.

        Returns:
            Raw bytes of the captured or generated PNG image.

        Raises:
            RuntimeError: If adb screencap execution fails with non-zero exit code.
        """
        if self.mock_mode:
            # Generate a 720x1280 mock screen frame
            img = Image.new("RGB", (720, 1280), color=(24, 26, 32))
            draw = ImageDraw.Draw(img)
            # Draw mock app bar
            draw.rectangle([0, 0, 720, 120], fill=(45, 55, 72))
            draw.text((40, 50), "GenAI Debugger Target App", fill=(255, 255, 255))
            # Draw mock crash notification
            draw.rectangle([60, 400, 660, 700], fill=(220, 38, 38))
            draw.text((90, 440), "FATAL EXCEPTION: NullPointerException", fill=(255, 255, 255))
            draw.text((90, 500), "FeedScreen.kt line 42", fill=(254, 202, 202))
            draw.text((90, 560), "uiState.userProfile.name is null", fill=(254, 202, 202))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        cmd = self._base_cmd() + ["exec-out", "screencap", "-p"]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=8)
            if res.returncode != 0:
                raise RuntimeError(f"ADB screencap failed: {res.stderr.decode('utf-8', errors='ignore')}")
            return res.stdout
        except Exception as e:
            logger.error(f"Error capturing screenshot: {e}")
            raise

    def clear_logcat(self) -> bool:
        """Clears the ring buffer of logcat on the device."""
        if self.mock_mode:
            return True
        cmd = self._base_cmd() + ["logcat", "-c"]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        return res.returncode == 0

    def start_package(self, package_name: str, activity_name: str | None = None) -> bool:
        """Launches an application package using am start or monkey fallback.

        Args:
            package_name: Target application package name (e.g. 'com.example.mobileapp').
            activity_name: Optional explicit activity name to launch.

        Returns:
            True if process launched successfully.
        """
        if self.mock_mode:
            logger.info(f"[Mock] Launched package {package_name}")
            return True
        target = f"{package_name}/{activity_name}" if activity_name else package_name
        cmd = self._base_cmd() + ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
        if activity_name:
            cmd = self._base_cmd() + ["shell", "am", "start", "-n", target]
        res = subprocess.run(cmd, capture_output=True, timeout=10)
        return res.returncode == 0

    def force_stop_package(self, package_name: str) -> bool:
        """Force stops an application package."""
        if self.mock_mode:
            logger.info(f"[Mock] Force stopped {package_name}")
            return True
        cmd = self._base_cmd() + ["shell", "am", "force-stop", package_name]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        return res.returncode == 0

    def clear_package_data(self, package_name: str) -> bool:
        """Clears application data via pm clear."""
        if self.mock_mode:
            logger.info(f"[Mock] Cleared data for {package_name}")
            return True
        cmd = self._base_cmd() + ["shell", "pm", "clear", package_name]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        return res.returncode == 0

    def reverse_port(self, device_port: int, host_port: int) -> bool:
        """Sets up adb reverse tcp:device_port tcp:host_port."""
        if self.mock_mode:
            logger.info(f"[Mock] Port reverse tcp:{device_port} -> tcp:{host_port}")
            return True
        cmd = self._base_cmd() + ["reverse", f"tcp:{device_port}", f"tcp:{host_port}"]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        return res.returncode == 0

    def get_dumpsys(self, service: str, args: list[str] | None = None) -> str:
        """Executes adb shell dumpsys <service> [args]."""
        if self.mock_mode:
            return f"Mock dumpsys output for service={service}"
        cmd = self._base_cmd() + ["shell", "dumpsys", service]
        if args:
            cmd.extend(args)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return res.stdout

    async def stream_logcat_lines(self) -> AsyncGenerator[str, None]:
        """Asynchronously streams lines from adb logcat -v threadtime."""
        if self.mock_mode:
            mock_lines = [
                "11-15 12:00:01.100  1000  1050 I ActivityManager: Start proc 2345:com.example.mobileapp/u0a120 for activity",
                "11-15 12:00:01.150  2345  2345 D ExampleApp: Initializing user session token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
                "11-15 12:00:01.200  2345  2345 I FeedScreen: Rendering feed items for user contact@example.com",
                "11-15 12:00:01.300  2345  2345 W ExampleApp: Slow frame render detected: 32ms frame time",
                "11-15 12:00:02.000  2345  2345 E AndroidRuntime: FATAL EXCEPTION: main",
                "11-15 12:00:02.001  2345  2345 E AndroidRuntime: Process: com.example.mobileapp, PID: 2345",
                "11-15 12:00:02.002  2345  2345 E AndroidRuntime: java.lang.NullPointerException: Attempt to invoke virtual method 'java.lang.String com.example.mobileapp.UserProfile.getName()' on a null object reference",
                "11-15 12:00:02.003  2345  2345 E AndroidRuntime: \tat com.example.mobileapp.ui.FeedScreenKt.FeedScreen(FeedScreen.kt:42)",
                "11-15 12:00:02.004  2345  2345 E AndroidRuntime: \tat com.example.mobileapp.MainActivity$onCreate$1$1.invoke(MainActivity.kt:28)",
                "11-15 12:00:02.005  2345  2345 E AndroidRuntime: \tat androidx.compose.runtime.internal.ComposableLambdaImpl.invoke(ComposableLambda.jvm.kt:116)",
            ]
            for line in mock_lines:
                await asyncio.sleep(0.05)
                yield line
            # Keep idling or yielding periodic heartbeats
            while True:
                await asyncio.sleep(1.0)
                yield "11-15 12:00:03.000  2345  2345 D Heartbeat: Device active"
            return

        cmd = self._base_cmd() + ["logcat", "-v", "threadtime"]
        self._active_logcat_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            while self._active_logcat_proc and self._active_logcat_proc.stdout:
                line_bytes = await self._active_logcat_proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    yield line
        except asyncio.CancelledError:
            if self._active_logcat_proc:
                try:
                    self._active_logcat_proc.terminate()
                except ProcessLookupError:
                    pass
            raise
        finally:
            if self._active_logcat_proc:
                try:
                    self._active_logcat_proc.kill()
                except ProcessLookupError:
                    pass
                self._active_logcat_proc = None

    def stop_streaming(self):
        """Terminates active logcat subprocess if running."""
        if self._active_logcat_proc:
            try:
                self._active_logcat_proc.kill()
            except ProcessLookupError:
                pass
            self._active_logcat_proc = None
