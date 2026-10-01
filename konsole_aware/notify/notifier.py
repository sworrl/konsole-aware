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

    def notify(
        self,
        title: str,
        message: str,
        urgency: str = "normal",
        is_error: bool = False,
        play_sound: bool = True,
        icon: str = "utilities-terminal",
    ) -> None:
        """Send desktop notification and optional audio cue."""
        if not self.config.get("enable_desktop_notifications", True):
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
                # Transient: pops up and fades without piling up in the notification history.
                "--hint=boolean:transient:true",
            ]
            if urgency != "critical" and timeout_s and float(timeout_s) > 0:
                cmd.extend(["-t", str(int(float(timeout_s) * 1000))])
            if sound_file and os.path.exists(sound_file):
                cmd.append(f"--hint=string:sound-file:{sound_file}")

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
