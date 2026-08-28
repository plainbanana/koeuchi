"""Sound feedback via async afplay of built-in macOS sounds."""

from __future__ import annotations

import subprocess

_SOUND_DIR = "/System/Library/Sounds"

SOUNDS = {
    "stop": "Pop",
    "done": "Glass",
    "error": "Basso",
}


class SoundFeedback:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled

    def play(self, event: str) -> None:
        if not self._enabled:
            return
        name = SOUNDS.get(event)
        if not name:
            return
        try:
            subprocess.Popen(
                ["afplay", f"{_SOUND_DIR}/{name}.aiff"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
