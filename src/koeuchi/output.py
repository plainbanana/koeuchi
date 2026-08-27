"""Output: copy to the clipboard and optionally send Cmd+V to the front app.

Never sends Enter, by design.
"""

from __future__ import annotations

import re
import time

import AppKit
import Quartz

_KEY_V = 9  # kVK_ANSI_V

# All C0/C1 control characters and DEL, so a paste into a terminal can never
# execute a command or inject escape sequences.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def sanitize(text: str) -> str:
    return _CONTROL_CHARS.sub(" ", text).strip()


def _copy_to_clipboard(text: str) -> None:
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)


def _send_cmd_v() -> None:
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(source, _KEY_V, True)
    up = Quartz.CGEventCreateKeyboardEvent(source, _KEY_V, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


class OutputController:
    def __init__(self, auto_paste: bool = True):
        self._auto_paste = auto_paste

    def deliver(self, text: str) -> None:
        text = sanitize(text)
        if not text:
            return
        _copy_to_clipboard(text)
        if self._auto_paste:
            time.sleep(0.01)
            _send_cmd_v()
