"""Windows support for SonicFX Ultimate.

The Windows build deliberately uses Equalizer APO as the real-time audio
engine.  Equalizer APO is a free, open-source, system-wide APO written in C++;
SonicFX only writes its configuration and never puts Python in the audio path.
"""
from __future__ import annotations

from sonicfx.windows.apo import ApoError, ApoStatus, EqualizerApo, render_apo_config

__all__ = ["ApoError", "ApoStatus", "EqualizerApo", "render_apo_config"]
