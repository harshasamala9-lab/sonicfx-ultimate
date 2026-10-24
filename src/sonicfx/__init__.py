"""SonicFX Ultimate — free & local system-wide audio enhancement for Linux.

Architecture (strict separation):
    Control plane  : Python (GUI, profiles, DB, PipeWire session inspection)
    Real-time plane: PipeWire (filter-chain module: builtin DSP ops + LADSPA/LV2)
No Python code runs inside the audio-processing callback path.
"""

__version__ = "1.0.0"
APP_NAME = "SonicFX Ultimate"
