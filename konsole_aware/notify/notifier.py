"""Desktop and audio notifications for tab events."""

import os
import shutil
import subprocess
from typing import Optional
from konsole_aware.config import load_config


class Notifier:
    """Dispatches desktop notifications and sounds."""

    def __init__(self):
        self.config = load_config()
        self.notify_send_bin = shutil.which("notify-send")
        self.sound_bin = shutil.which("paplay") or shutil.which("pw-play") or shutil.which("aplay")

    def play_sound(self, sound_path: str) -> None:
        """Play sound file asynchronously in background."""
        if not self.config.get("alert_sound", True) or not self.sound_bin:
            return
        if not sound_path or not os.path.exists(sound_path):
            return

        try:
            subprocess.Popen(
                [self.sound_bin, sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass

    def _allow(self) -> bool:
        """Global rate limit across every hook process, so a burst of agents finishing at
        once (or a misbehaving loop) can never flood plasmashell's notification server:
        at most `notification_burst` toasts per `notification_window_seconds`."""
        import fcntl
        import json
        import time
        from konsole_aware.state.manager import STATE_DIR
        limit = int(self.config.get("notification_burst", 4))
        window = float(self.config.get("notification_window_seconds", 20))
        if limit <= 0:
            return True
        try:
            STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(STATE_DIR / "notify-times.json", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 65536)
            try:
                times = [t for t in json.loads(raw or b"[]") if isinstance(t, (int, float))]
            except ValueError:
                times = []
            now = time.time()
            times = [t for t in times if now - t < window]
            if len(times) >= limit:
                return False
            times.append(now)
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(times).encode())
            return True
        finally:
            os.close(fd)

    def notify(
        self,
        title: str,
        message: str,
        urgency: str = "normal",
        is_error: bool = False,
        play_sound: bool = True,
        icon: str = "utilities-terminal",
        transient: bool = True,
    ) -> None:
        """Send desktop notification and optional audio cue."""
        if not self.config.get("enable_desktop_notifications", True):
            return
        if not self._allow():
            return

        sound_file = (
            self.config.get("alert_sound_error_file")
            if is_error
            else self.config.get("alert_sound_file")
        )
        if not play_sound or not self.config.get("alert_sound", True):
            sound_file = None

        if self.notify_send_bin:
            if urgency == "critical" and not self.config.get("sticky_alerts", False):
                urgency = "normal"
            timeout_s = self.config.get("notification_timeout_seconds", 4)
            cmd = [
                self.notify_send_bin,
                "-a", "Konsole Aware",
                "-i", icon,
                "-u", urgency,
            ]
            if transient:
                # Pops up and fades without piling up in the notification history.
                cmd.append("--hint=boolean:transient:true")
            if urgency != "critical" and timeout_s and float(timeout_s) > 0:
                cmd.extend(["-t", str(int(float(timeout_s) * 1000))])
            if sound_file:
                # We play the sound ourselves below; tell the server not to add its own.
                cmd.append("--hint=boolean:suppress-sound:true")

            cmd.extend([title, message])

            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

        if play_sound and sound_file:
            self.play_sound(sound_file)
