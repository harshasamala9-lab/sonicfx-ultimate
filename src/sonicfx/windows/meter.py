"""WASAPI loopback peak meter for the Windows backend.

This is a monitoring path only: it reads a loopback copy of an output endpoint
and never touches the audio that Equalizer APO processes.  ``soundcard`` is an
optional Windows dependency; when it is missing the app reports the meter as
unavailable instead of inventing a level.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True)
class WindowsOutput:
    """One Windows render endpoint shown in the UI."""

    name: str
    description: str
    is_default: bool = False
    serial: int = 0


def _soundcard():
    import soundcard  # type: ignore[import-not-found]

    return soundcard


def available_speakers() -> list[WindowsOutput]:
    """List Windows playback endpoints, newest/default first.

    Returns an empty list on non-Windows systems or when ``soundcard``/WASAPI
    is unavailable.  The UI then keeps a synthetic "Default output" entry.
    """
    try:
        sc = _soundcard()
        default = sc.default_speaker()
        default_id = str(default.id) if default is not None else None
        speakers = list(sc.all_speakers())
    except Exception:  # noqa: BLE001 - optional dependency / no audio device
        return []
    results: list[WindowsOutput] = []
    for speaker in speakers:
        name = str(speaker.id)
        results.append(
            WindowsOutput(
                name=name,
                description=str(speaker.name),
                is_default=name == default_id,
            )
        )
    results.sort(key=lambda item: (not item.is_default, item.description.casefold()))
    return results


class WindowsMeterWorker(QThread):
    """Read a WASAPI loopback stream and emit L/R peak amplitudes."""

    levels = Signal(float, float)
    unavailable = Signal(str)

    READ_FRAMES = 4_800
    SAMPLE_RATE = 48_000
    RETRY_S = 0.75
    IDLE_S = 0.25

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._device_id: str | None = None
        self._device_lock = threading.Lock()
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._last_error = ""

    def set_device(self, device_id: str | None) -> None:
        with self._device_lock:
            self._device_id = device_id or None
        self._changed.set()

    def stop(self) -> None:
        self._stop.set()
        self._changed.set()

    def run(self) -> None:
        try:
            import numpy as np

            sc = _soundcard()
        except Exception as exc:  # noqa: BLE001
            self.unavailable.emit(
                f"WASAPI loopback metering is unavailable: {exc}. "
                "Install the optional 'soundcard' package on Windows."
            )
            return

        while not self._stop.is_set():
            with self._device_lock:
                device_id = self._device_id
            if not device_id:
                self._changed.clear()
                self.levels.emit(0.0, 0.0)
                self._stop.wait(self.IDLE_S)
                continue
            try:
                speaker = self._find_speaker(sc, device_id)
                if speaker is None:
                    self.levels.emit(0.0, 0.0)
                    if self._stop.wait(self.RETRY_S):
                        break
                    continue
                microphone = sc.get_microphone(
                    id=str(speaker.id), include_loopback=True
                )
                with microphone.recorder(
                    samplerate=self.SAMPLE_RATE,
                    channels=2,
                    blocksize=self.READ_FRAMES,
                ) as recorder:
                    self._last_error = ""
                    while not self._stop.is_set():
                        with self._device_lock:
                            if self._device_id != device_id:
                                break
                        data = recorder.record(numframes=self.READ_FRAMES)
                        peaks = self._peaks(np, data)
                        self.levels.emit(peaks[0], peaks[1])
            except Exception as exc:  # noqa: BLE001 - device can disappear
                message = str(exc)
                if message != self._last_error:
                    self._last_error = message
                    self.unavailable.emit(f"Windows peak meter paused: {message}")
                self.levels.emit(0.0, 0.0)
                if self._stop.wait(self.RETRY_S):
                    break
        self.levels.emit(0.0, 0.0)

    @staticmethod
    def _find_speaker(sc, device_id: str):
        try:
            return sc.get_speaker(device_id)
        except Exception:  # noqa: BLE001 - older soundcard versions
            pass
        try:
            for speaker in sc.all_speakers():
                if str(speaker.id) == device_id or str(speaker.name) == device_id:
                    return speaker
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _peaks(np, data) -> tuple[float, float]:
        values = np.asarray(data, dtype=float)
        if values.size == 0:
            return (0.0, 0.0)
        if values.ndim == 1:
            peak = float(np.max(np.abs(values)))
            return (peak, peak)
        peak = np.max(np.abs(values), axis=0)
        if peak.size == 1:
            return (float(peak[0]), float(peak[0]))
        return (float(peak[0]), float(peak[1]))
