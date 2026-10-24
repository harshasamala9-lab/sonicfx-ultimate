"""Offline DSP design & validation plane for SonicFX Ultimate.

These modules design filter parameters, synthesize impulse responses, run
meter math and perform acoustic sanity checks.  They are used by:

* the GUI (EQ curve rendering, meter readouts, GR estimate),
* the PipeWire chain generator (design values -> filter-chain config),
* the automated test suite.

None of these functions process live audio payload in the real-time path —
that role belongs exclusively to the PipeWire filter-chain (C) implementation.
"""
from __future__ import annotations

__all__ = ["filters", "metering", "dynamics", "irs", "widening"]
