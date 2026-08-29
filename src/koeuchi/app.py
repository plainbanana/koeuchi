"""Entry point: record while the hotkey is held, paste the ASR result."""

from __future__ import annotations

import argparse
import json
import os
import textwrap
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
    reference,
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


class Reporter:
    """App-level log lines: human text, or one JSON object per line."""

    def __init__(self, as_json: bool):
        self._as_json = as_json

    def emit(self, event: str, human: str, **data) -> None:
        if self._as_json:
            print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)
        else:
            print(human, flush=True)


class App:
    def __init__(self, config: Config, out: Reporter):
        self._config = config
        self._out = out
        self._recorder = Recorder(sample_rate=config.sample_rate)
        self._asr: ASRBackend = Qwen3ASRBackend(
            config.model,
            revision=config.model_revision,
            language=config.language,
            bias_text=config.bias_text,
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
                self._discard(f"✗ 録音エラー: {e}", event="error")

    def _on_stop_button(self) -> None:
        if self._listener is not None:
            self._listener.force_release()

    def _start_recording(self) -> None:
        self._recorder.start()
        self._recording = True
        self._status_item.set_state("recording")
        self._overlay.show_recording()
        self._out.emit("recording", "● 録音中...")

    def _discard(self, message: str, event: str = "discarded") -> None:
        self._feedback.play("error")
        # While a new recording is underway, keep the waveform UI; the failed
        # job only concerns a previous take.
        if not self._recording:
            self._status_item.set_state("idle")
            self._overlay.hide()
        self._out.emit(event, message, message=message)

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
                self._discard(f"✗ ASRエラー: {e}", event="error")
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
            self._out.emit(
                "result",
                f"✓ [{duration:.1f}s→{asr_time:.2f}s] {text}",
                text=text,
                duration=round(duration, 1),
                asr_time=round(asr_time, 2),
            )

    def _restart(self, reason: str) -> None:
        """Re-exec the whole process, discarding hung threads. Last resort."""
        self._out.emit("restarting", f"⟳ {reason}、再起動します", reason=reason)
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
        self._out.emit("shutdown", "\n終了します")
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
        self._out.emit("startup", f"koeuchi {app_version()}", version=app_version())
        self._out.emit(
            "model_loading", f"モデルロード中: {self._config.model}", model=self._config.model
        )
        t0 = time.monotonic()
        self._asr.warmup()
        load_time = time.monotonic() - t0
        self._out.emit(
            "model_ready", f"モデル準備完了 ({load_time:.1f}s)", seconds=round(load_time, 1)
        )
        if self._shutdown.is_set():
            sys.stdout.flush()
            os._exit(0)

        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._key_worker, daemon=True).start()
        self._listener = HotkeyListener(self._config.hotkey, self._on_press, self._on_release)
        self._listener.start()
        self._out.emit(
            "ready",
            f"待機中: [{self._config.hotkey}] を押している間だけ録音します "
            f"(auto_paste={self._config.auto_paste}, config={CONFIG_PATH})",
            hotkey=self._config.hotkey,
            auto_paste=self._config.auto_paste,
            config=str(CONFIG_PATH),
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


_HOW_IT_WORKS = (
    "koeuchi waits in the background and records the microphone only while "
    "the hotkey is held down. On release, the audio is transcribed by a local "
    "ASR model and the result is copied to the clipboard (with auto_paste=true "
    "it also sends Cmd+V to the frontmost app)."
)
_CONFIG_FORMAT = (
    "TOML. If the file does not exist, all defaults are used. Unknown keys "
    "are ignored. Every key can also be set via a command-line option, which "
    "takes precedence over the config file."
)
_PERMISSIONS = [
    "Microphone (System Settings > Privacy & Security > Microphone)",
    "Accessibility (same > Accessibility); needed for the global hotkey "
    "listener and for auto_paste keystrokes",
]

_HELP_EPILOG = """\
How it works:
{how_it_works}

Config file:
{config_file}

Config keys and defaults:
{config_keys}

Required macOS permissions (grant to the terminal app that launches koeuchi):
{permissions}
"""


def _wrap(text: str, first: str = "  ", rest: str = "  ") -> str:
    return textwrap.fill(text, width=78, initial_indent=first, subsequent_indent=rest)


def _epilog() -> str:
    return _HELP_EPILOG.format(
        how_it_works=_wrap(_HOW_IT_WORKS),
        config_file=_wrap(f"{CONFIG_PATH} — {_CONFIG_FORMAT}"),
        config_keys=describe_keys(),
        permissions="\n".join(_wrap(p, first="  - ", rest="    ") for p in _PERMISSIONS),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="koeuchi",
        description="Japanese push-to-talk voice input with local ASR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    actions = [
        parser.add_argument(
            "-h",
            "--help",
            action="store_true",
            help="show this help message and exit (as JSON with --json)",
        ),
        parser.add_argument(
            "--version", action="version", version=f"koeuchi {app_version()}"
        ),
        parser.add_argument(
            "--json",
            action="store_true",
            help="machine-readable output: run logs become one JSON object per line",
        ),
        *add_cli_options(parser),
    ]
    args = parser.parse_args()
    if args.help:
        if args.json:
            print(
                json.dumps(
                    {
                        "name": "koeuchi",
                        "version": app_version(),
                        "description": parser.description,
                        "options": [
                            {"flags": action.option_strings, "description": action.help}
                            for action in actions
                        ],
                        "how_it_works": _HOW_IT_WORKS,
                        "config_path": str(CONFIG_PATH),
                        "config_format": _CONFIG_FORMAT,
                        "config_keys": reference(),
                        "macos_permissions": _PERMISSIONS,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            parser.epilog = _epilog()
            parser.print_help()
        return
    if args.json:
        # Progress bars go to stderr and stdout stays parseable either way,
        # but they make no sense next to a JSONL stream.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    App(apply_cli_overrides(Config.load(), args), Reporter(as_json=args.json)).run()


if __name__ == "__main__":
    main()
