"""Global hotkey watcher.

Callbacks run on the macOS event-tap thread; they must return immediately or
the OS disables the tap and the hotkey goes dead.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

import Quartz
from pynput import keyboard

_WATCH_INTERVAL = 2.0
_START_TIMEOUT = 5.0


class _Listener(keyboard.Listener):
    """Keeps a reference to the created event tap for health checks."""

    _tap = None

    def _create_event_tap(self):
        self._tap = super()._create_event_tap()
        return self._tap


class HotkeyListener:
    def __init__(self, key_name: str, on_press: Callable[[], None], on_release: Callable[[], None]):
        try:
            self._key = getattr(keyboard.Key, key_name)
        except AttributeError:
            raise ValueError(
                f"不明なキー名: {key_name!r} (pynput.keyboard.Key の属性名を指定してください)"
            ) from None
        self._on_press = on_press
        self._on_release = on_release
        self._pressed = False
        self._stopping = threading.Event()
        self._listener = _Listener(on_press=self._handle_press, on_release=self._handle_release)

    def _handle_press(self, key) -> None:
        if key == self._key and not self._pressed:
            self._pressed = True
            self._on_press()

    def _handle_release(self, key) -> None:
        if key == self._key and self._pressed:
            self._pressed = False
            self._on_release()

    def force_release(self) -> None:
        if self._pressed:
            self._pressed = False
            self._on_release()

    def _watch(self) -> None:
        # macOS disables the tap on sleep/wake or slow callbacks and pynput
        # never re-enables it, leaving the hotkey dead; re-enable it here.
        while not self._stopping.wait(_WATCH_INTERVAL):
            tap = self._listener._tap
            if tap is None or Quartz.CGEventTapIsEnabled(tap):
                continue
            print("[hotkey] イベントタップが無効化されたため再有効化します", file=sys.stderr, flush=True)
            Quartz.CGEventTapEnable(tap, True)
            # A key-up may have been missed while disabled.
            self.force_release()

    def start(self) -> None:
        self._listener.start()
        deadline = time.monotonic() + _START_TIMEOUT
        while self._listener._tap is None:
            if not self._listener.is_alive():
                raise RuntimeError(
                    "キーイベントを監視できません。システム設定 > プライバシーとセキュリティ > "
                    "アクセシビリティ にターミナルアプリを追加してください"
                )
            if time.monotonic() > deadline:
                raise RuntimeError("ホットキー監視の初期化がタイムアウトしました")
            time.sleep(0.05)
        threading.Thread(target=self._watch, daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()
        self._listener.stop()
