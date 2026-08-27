"""Microphone capture. An InputStream is open only while the key is held.

PortAudio stream operations are slow (open ~55ms, stop ~105ms) and not safe to
call concurrently, so they are serialized onto a single worker thread and
callers never wait for them.
"""

from __future__ import annotations

import collections
import itertools
import math
import queue
import threading
import time

import numpy as np
import sounddevice as sd

BLOCKSIZE = 256  # 16ms at 16kHz; the default (0) yields >1000 callbacks/sec
_CLOSE_TIMEOUT = 0.5
# Opens run before closes so a pending stop never delays the next recording.
_PRIO_OPEN, _PRIO_CLOSE = 0, 1
_LEVEL_HISTORY = 64
# Held longer than this with zero samples captured means the stream is dead.
_DEAD_AFTER = 0.5
_LEVEL_FLOOR_DB = -50.0
_LEVEL_CEIL_DB = -12.0


def _level(block: np.ndarray) -> float:
    """Map one block's RMS to 0.0-1.0 on a dB scale."""
    rms = float(np.sqrt(np.mean(np.square(block))))
    if rms <= 0.0:
        return 0.0
    db = 20.0 * math.log10(rms)
    return min(1.0, max(0.0, (db - _LEVEL_FLOOR_DB) / (_LEVEL_CEIL_DB - _LEVEL_FLOOR_DB)))


class _Capture:
    """One recording. A fresh instance per recording keeps a stale stream's
    callback from polluting the next recording's buffer."""

    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._active = True
        self._levels: collections.deque[float] = collections.deque(
            [0.0] * _LEVEL_HISTORY, maxlen=_LEVEL_HISTORY
        )
        self.stream: sd.InputStream | None = None

    def callback(self, indata: np.ndarray, frames, time_info, status) -> None:
        if status:
            print(f"[recorder] status: {status}", flush=True)
        if not self._active:
            return
        block = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(block)
        self._levels.append(_level(block))

    def levels(self) -> list[float]:
        return list(self._levels)

    def take(self) -> np.ndarray:
        """Cut off accumulation and return the waveform without waiting for
        the stream to stop."""
        self._active = False
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)


class Recorder:
    def __init__(self, sample_rate: int = 16000, blocksize: int = BLOCKSIZE):
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._capture: _Capture | None = None
        self._reinit = False
        self._started = 0.0
        self._busy: tuple[str, float] | None = None
        self._ops: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        threading.Thread(target=self._ops_worker, daemon=True).start()

    def _ops_worker(self) -> None:
        while True:
            _, _, op, capture, done = self._ops.get()
            self._busy = (op, time.monotonic())
            try:
                if op == "sync":
                    pass
                elif op in ("open", "reopen"):
                    if op == "reopen":
                        # PortAudio snapshots the device list at init; after the
                        # default input changes it must be re-initialized or it
                        # keeps capturing silence.
                        sd._terminate()
                        sd._initialize()
                        print("[recorder] PortAudioを再初期化しました", flush=True)
                    stream = sd.InputStream(
                        samplerate=self._sample_rate,
                        blocksize=self._blocksize,
                        channels=1,
                        dtype="float32",
                        callback=capture.callback,
                    )
                    stream.start()
                    capture.stream = stream
                elif capture is not None and capture.stream is not None:
                    capture.stream.stop()
                    capture.stream.close()
                    capture.stream = None
            except Exception as e:
                is_open = op in ("open", "reopen")
                if is_open:
                    self._reinit = True
                print(f"✗ 録音{'開始' if is_open else '停止'}に失敗: {e}", flush=True)
            finally:
                self._busy = None
                if done is not None:
                    done.set()

    def _submit(self, prio: int, op: str, capture: _Capture | None, done=None) -> None:
        self._ops.put((prio, next(self._seq), op, capture, done))

    def _discard(self) -> None:
        capture, self._capture = self._capture, None
        if capture is None:
            return
        capture.take()
        self._submit(_PRIO_CLOSE, "close", capture)

    def stalled(self, threshold: float = 2.0) -> bool:
        """Whether the ops thread is stuck on one operation (a hang inside C
        cannot be recovered from Python; callers restart the process)."""
        busy = self._busy
        return busy is not None and time.monotonic() - busy[1] >= threshold

    def levels(self) -> list[float]:
        capture = self._capture
        return capture.levels() if capture is not None else [0.0] * _LEVEL_HISTORY

    def start(self) -> None:
        self._discard()
        capture = _Capture()
        self._capture = capture
        self._started = time.monotonic()
        if self._reinit:
            # Re-init is only safe after all streams are closed, so queue it
            # behind the pending closes.
            self._reinit = False
            self._submit(_PRIO_CLOSE, "reopen", capture)
        else:
            self._submit(_PRIO_OPEN, "open", capture)

    def stop(self) -> np.ndarray:
        """Cut off the recording and return float32 mono audio; the stream is
        stopped in the background."""
        capture, self._capture = self._capture, None
        if capture is None:
            return np.zeros(0, dtype=np.float32)
        audio = capture.take()
        self._submit(_PRIO_CLOSE, "close", capture)
        elapsed = time.monotonic() - self._started
        if elapsed >= _DEAD_AFTER and audio.size == 0:
            self._reinit = True
            print("[recorder] 無音ストリームを検出。次の録音でPortAudioを再初期化します", flush=True)
        return audio

    def close(self) -> None:
        """Close any open stream and wait for pending background closes."""
        self._discard()
        done = threading.Event()
        self._submit(_PRIO_CLOSE, "sync", None, done)
        done.wait(timeout=_CLOSE_TIMEOUT)
