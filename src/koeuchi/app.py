"""Entry point: record while the hotkey is held, paste the ASR result."""

from __future__ import annotations

import argparse
import os
import queue
import signal
import sys
import threading
import time

import AppKit
import numpy as np
from PyObjCTools import AppHelper

from .asr import ASRBackend, Qwen3ASRBackend
from .config import (
    CONFIG_PATH,
    Config,
    add_cli_options,
    apply_cli_overrides,
    describe_keys,
)
from .feedback import SoundFeedback
from .hotkey import HotkeyListener
from .output import OutputController
from .processor import Replacer, TranscriptProcessor
from .recorder import Recorder
from .statusbar import NullStatusItem, StatusItem
from .ui import NullOverlay, Overlay, schedule_timer
from .version import app_version

_CLOSE_TIMEOUT = 1.0
_RESULT_FLASH_BASE = 2.5
_RESULT_FLASH_PER_CHAR = 0.05
_RESULT_FLASH_MAX = 10.0
_TICK = 0.2
_PROGRESS_MIN_DURATION = 5.0
_CHARS_PER_SEC = 7.0  # measured Japanese speech rate, for progress estimation


class App:
    def __init__(self, config: Config):
        self._config = config
        self._recorder = Recorder(sample_rate=config.sample_rate)
        self._asr: ASRBackend = Qwen3ASRBackend(
            config.model,
            revision=config.model_revision,
            language=config.language,
        )
        self._processor: TranscriptProcessor = Replacer(config.replacements)
        self._shutdown = threading.Event()
        self._output = OutputController(auto_paste=config.auto_paste)
        self._feedback = SoundFeedback(enabled=config.sounds)
        self._overlay = (
            Overlay(levels=self._recorder.levels, on_stop=self._on_stop_button)
            if config.overlay
            else NullOverlay()
        )
        self._status_item = (
            StatusItem(version=app_version(), on_quit=self._shutdown.set)
            if config.menu_bar
            else NullStatusItem()
        )
        self._listener: HotkeyListener | None = None
        self._recording = False
        self._jobs: queue.Queue = queue.Queue()
        # Doing recorder start/stop on the event-tap thread risks macOS
        # disabling the tap when it runs slow; forward key edges in order to a
        # separate thread instead.
        self._keys: queue.Queue = queue.Queue()

    def _on_press(self) -> None:
        self._keys.put(True)

    def _on_release(self) -> None:
        self._keys.put(False)

    def _key_worker(self) -> None:
        while True:
            pressed = self._keys.get()
            try:
                if pressed:
                    self._start_recording()
                else:
                    self._finish_recording()
            except Exception as e:
                self._discard(f"✗ 録音エラー: {e}")

    def _on_stop_button(self) -> None:
        if self._listener is not None:
            self._listener.force_release()

    def _start_recording(self) -> None:
        self._recorder.start()
        self._recording = True
        self._status_item.set_state("recording")
        self._overlay.show_recording()
        print("● 録音中...", flush=True)

    def _discard(self, message: str) -> None:
        self._feedback.play("error")
        # While a new recording is underway, keep the waveform UI; the failed
        # job only concerns a previous take.
        if not self._recording:
            self._status_item.set_state("idle")
            self._overlay.hide()
        print(message, flush=True)

    def _finish_recording(self) -> None:
        self._recording = False
        audio = self._recorder.stop()
        duration = len(audio) / self._config.sample_rate
        if duration < self._config.min_duration:
            self._discard(f"○ 破棄 ({duration:.2f}s < {self._config.min_duration}s)")
            if audio.size == 0 and self._recorder.stalled():
                self._restart("オーディオデバイスの操作が応答しないため")
            return
        if float(np.abs(audio).max()) < 0.01:
            self._discard(f"○ 無音のため破棄 ({duration:.1f}s)")
            return
        self._feedback.play("stop")
        self._status_item.set_state("transcribing")
        self._overlay.show_message("文字起こし中…")
        self._jobs.put((audio, duration))

    def _make_progress(self, duration: float):
        expected_chars = max(1.0, duration * _CHARS_PER_SEC)

        def on_progress(partial: str) -> None:
            if self._recording:
                return
            pct = min(99, int(len(partial) / expected_chars * 100))
            self._overlay.show_message(f"文字起こし中… {pct}%")

        return on_progress

    def _worker(self) -> None:
        while True:
            audio, duration = self._jobs.get()
            t0 = time.monotonic()
            on_progress = (
                self._make_progress(duration)
                if duration >= _PROGRESS_MIN_DURATION
                else None
            )
            try:
                text = self._asr.transcribe(audio, on_progress)
            except Exception as e:
                self._discard(f"✗ ASRエラー: {e}")
                continue
            asr_time = time.monotonic() - t0
            raw = text
            text = self._processor.process(text)
            if not text:
                self._discard(f"○ 破棄 (録音{duration:.1f}s / ASR {asr_time:.2f}s) {raw!r}")
                continue
            self._output.deliver(text)
            self._feedback.play("done")
            if not self._recording:
                self._status_item.set_state("idle")
                flash = min(
                    _RESULT_FLASH_MAX, _RESULT_FLASH_BASE + len(text) * _RESULT_FLASH_PER_CHAR
                )
                self._overlay.flash_message(f"✓ {text}", flash)
            print(f"✓ [{duration:.1f}s→{asr_time:.2f}s] {text}", flush=True)

    def _restart(self, reason: str) -> None:
        """Re-exec the whole process, discarding hung threads. Last resort."""
        print(f"⟳ {reason}、再起動します", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, "-m", "koeuchi.app", *sys.argv[1:]])

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame) -> None:
            # Restore the default action so a second Ctrl+C kills the process
            # even when the GIL is held by ASR inference.
            signal.signal(signum, signal.SIG_DFL)
            self._shutdown.set()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _shutdown_now(self) -> None:
        print("\n終了します", flush=True)
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass
        closer = threading.Thread(target=self._recorder.close, daemon=True)
        closer.start()
        closer.join(timeout=_CLOSE_TIMEOUT)
        sys.stdout.flush()
        sys.stderr.flush()
        # atexit hooks (PortAudio terminate, MLX/Metal teardown) can hang, so
        # exit without running them.
        os._exit(0)

    def run(self) -> None:
        self._install_signal_handlers()
        print(f"koeuchi {app_version()}", flush=True)
        print(f"モデルロード中: {self._config.model}", flush=True)
        t0 = time.monotonic()
        self._asr.warmup()
        print(f"モデル準備完了 ({time.monotonic() - t0:.1f}s)", flush=True)
        if self._shutdown.is_set():
            sys.stdout.flush()
            os._exit(0)

        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._key_worker, daemon=True).start()
        self._listener = HotkeyListener(self._config.hotkey, self._on_press, self._on_release)
        self._listener.start()
        print(
            f"待機中: [{self._config.hotkey}] を押している間だけ録音します "
            f"(auto_paste={self._config.auto_paste}, config={CONFIG_PATH})",
            flush=True,
        )
        self._run_event_loop()
        self._shutdown_now()

    def _run_event_loop(self) -> None:
        # Python signal handlers do not run while inside the Cocoa event loop;
        # a periodic timer returns control so Ctrl+C is not lost.
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._status_item.build()
        self._overlay.build()
        self._tick_timer = schedule_timer(_TICK, True, lambda _timer: self._tick())
        AppHelper.runEventLoop()

    def _tick(self) -> None:
        if self._shutdown.is_set():
            self._shutdown_now()


_HELP_EPILOG = """\
How it works:
  koeuchi waits in the background and records the microphone only while the
  hotkey is held down. On release, the audio is transcribed by a local ASR
  model and the result is copied to the clipboard (with auto_paste=true it
  also sends Cmd+V to the frontmost app).

Config file:
  ~/.config/koeuchi/config.toml (TOML). If it does not exist, all defaults
  are used. Unknown keys are ignored. Every key can also be set via the
  command-line options above, which take precedence over the config file.

Config keys and defaults:
{config_keys}

Required macOS permissions (grant to the terminal app that launches koeuchi):
  - Microphone (System Settings > Privacy & Security > Microphone)
  - Accessibility (same > Accessibility); needed for the global hotkey
    listener and for auto_paste keystrokes
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="koeuchi",
        description="Japanese push-to-talk voice input with local ASR",
        epilog=_HELP_EPILOG.format(config_keys=describe_keys()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"koeuchi {app_version()}"
    )
    add_cli_options(parser)
    args = parser.parse_args()
    App(apply_cli_overrides(Config.load(), args)).run()


if __name__ == "__main__":
    main()
