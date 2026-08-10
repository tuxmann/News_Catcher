"""VLC-based audio playback with seek for the News Catcher GUI."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import vlc

logger = logging.getLogger(__name__)


def mp3_duration_seconds(path: Path) -> float:
    path = Path(path)
    if not shutil.which("ffprobe"):
        return 0.0
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return max(0.0, float(out.stdout.strip()))
    except (subprocess.SubprocessError, ValueError) as exc:
        logger.warning("ffprobe duration failed: %s", exc)
        return 0.0


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


class GuiAudioPlayer:
    def __init__(self) -> None:
        self._instance = vlc.Instance("--no-video", "--quiet")
        self._player = self._instance.media_player_new()
        self._path: Path | None = None
        self._duration_hint = 0.0

    @property
    def path(self) -> Path | None:
        return self._path

    def load(self, path: Path) -> None:
        self.stop()
        self._path = Path(path)
        self._duration_hint = mp3_duration_seconds(self._path)
        media = self._instance.media_new(str(self._path))
        self._player.set_media(media)

    def play(self) -> None:
        if self._path is None:
            return
        self._player.play()

    def pause(self) -> None:
        self._player.set_pause(1)

    def resume(self) -> None:
        self._player.set_pause(0)

    def toggle_pause(self) -> bool:
        """Toggle pause; returns True if audio is now playing."""
        if self._path is None:
            return False
        if self.is_playing():
            self.pause()
            return False
        state = self._player.get_state()
        if state == vlc.State.Paused:
            self.resume()
            return True
        pos = self.get_position()
        duration = self.get_duration()
        if duration > 0 and pos >= duration - 0.5:
            self.set_position(0.0)
        self.play()
        return True

    def is_playing(self) -> bool:
        return bool(self._player.is_playing())

    def get_position(self) -> float:
        ms = self._player.get_time()
        if ms < 0:
            return 0.0
        return ms / 1000.0

    def get_duration(self) -> float:
        length = self._player.get_length()
        if length and length > 0:
            return length / 1000.0
        return self._duration_hint

    def set_position(self, seconds: float) -> None:
        duration = self.get_duration()
        if duration > 0:
            seconds = max(0.0, min(seconds, duration))
        self._player.set_time(int(seconds * 1000))

    def seek_relative(self, delta: float) -> None:
        self.set_position(self.get_position() + delta)

    def stop(self) -> None:
        self._player.stop()
