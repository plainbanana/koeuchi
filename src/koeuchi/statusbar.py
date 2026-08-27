"""Menu bar status item reflecting the idle / recording / transcribing state."""

from __future__ import annotations

from typing import Callable

import AppKit
from PyObjCTools import AppHelper

_SYMBOLS = {
    "idle": "mic",
    "recording": "mic.fill",
    "transcribing": "waveform",
}
_FALLBACK_TITLES = {
    "idle": "🎙",
    "recording": "🔴",
    "transcribing": "⏳",
}


class _MenuHandler(AppKit.NSObject):
    on_quit: Callable[[], None] | None = None

    def quit_(self, sender):
        if self.on_quit is not None:
            self.on_quit()


class StatusItem:
    """Menu bar controller. set_state may be called from any thread."""

    def __init__(self, on_quit: Callable[[], None]):
        self._on_quit = on_quit
        self._item = None
        self._handler = None

    def build(self) -> None:
        """Create the status item; call on the main thread before the event loop."""
        self._item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSSquareStatusItemLength
        )
        self._handler = _MenuHandler.alloc().init()
        self._handler.on_quit = self._on_quit
        menu = AppKit.NSMenu.alloc().init()
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "koeuchi を終了", "quit:", "q"
        )
        quit_item.setTarget_(self._handler)
        menu.addItem_(quit_item)
        self._item.setMenu_(menu)
        self._apply("idle")

    def set_state(self, state: str) -> None:
        AppHelper.callAfter(self._apply, state)

    def _apply(self, state: str) -> None:
        if self._item is None:
            return
        button = self._item.button()
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            _SYMBOLS[state], "koeuchi"
        )
        if image is None:
            button.setImage_(None)
            button.setTitle_(_FALLBACK_TITLES[state])
            return
        if state == "recording":
            palette = AppKit.NSImageSymbolConfiguration.configurationWithPaletteColors_(
                [AppKit.NSColor.systemRedColor()]
            )
            image = image.imageWithSymbolConfiguration_(palette)
            image.setTemplate_(False)
        else:
            image.setTemplate_(True)
        button.setImage_(image)
        button.setTitle_("")


class NullStatusItem:
    """No-op implementation used when menu_bar=false."""

    def build(self) -> None: ...
    def set_state(self, state: str) -> None: ...
