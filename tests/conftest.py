"""Shared fixtures for SonicFX DSP tests."""
from __future__ import annotations

import numpy as np
import pytest

FS = 48_000.0


@pytest.fixture
def fs() -> float:
    return FS


def sine(freq: float, seconds: float, fs: float = FS, amp: float = 1.0,
         phase_deg: float = 0.0) -> np.ndarray:
    """Deterministic sine helper (≥ 5 periods, integer-period padding optional)."""
    t = np.arange(int(seconds * fs)) / fs
    return amp * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))


def stereo(mono: np.ndarray, delay_ms: float = 0.0, fs: float = FS,
           amp_r: float = 1.0, invert: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Stereo pair from a mono source; optional delay/polarity on the right channel."""
    r = np.roll(mono, int(delay_ms / 1000.0 * fs)) * amp_r
    if invert:
        r = -r
    if delay_ms > 0:
        r[: int(delay_ms / 1000.0 * fs)] = 0.0
    return mono, r
