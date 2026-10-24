# Stage 4 results — GUI, system-wide default routing, autostart

Date: 2026-10-24
System: Ubuntu 26.04.1 (Wayland), PipeWire 1.6.2, WirePlumber 0.5.13, 48 kHz
Scope: ship a real desktop GUI whose controls change the engine, and let the user
make SonicFX the **default output** without ever losing their previous device.

![SonicFX Ultimate window](ui-preview.png)

*UI preview rendered offscreen with a fake controller (no live audio); the real
window uses the same layout and values.*

## What was built

| Area | Implementation |
|---|---|
| Window | PySide6 single-window "SonicFX Ultimate" UI, dark theme, offscreen-renderable |
| Presets | 9 built-in cards (including **Best All In One**); selecting one applies its real band set |
| Fine tune | One gain slider per preset band (0.5 dB steps, ±12 dB), live designed-response curve |
| Engine controls | Start/Stop in the header; status chip and stats reflect observed engine state |
| Output device | Real sink picker (`pw-dump`) + refresh; explicit `pw-link` to the chosen device |
| Peak meter | Live L/R peak bars + "Peak" readout; passive `*.monitor` capture (`pw-record`) of the *active output device*, driven by a dedicated worker QThread; works for Bluetooth and wired devices alike |
| System routing | "Use SonicFX for all system audio" toggle; ON by default for this profile |
| Login | XDG autostart desktop entry, created on first run, toggleable in-app |
| Persistence | QSettings: preset, band gains, output device, routing and autostart preferences |
| Threading | All engine calls on a worker `QThread`; UI never blocks on PipeWire |
| Debounce | Band/preset edits are collected for 300 ms, then one supervised restart |

The window never touches PipeWire directly: it calls a typed controller, which
calls the same `Engine` used by the Stage 3 measurements. There is no simulated
meter, no fake "Dolby" mode and no DSP in Python.

## Live Peak meter

The engine card shows a two-bar level meter and a numeric **Peak** readout.

* Source: a read-only `pw-record` capture on `<active output device>.monitor`
  — the same audio that reaches the JBL/Bluetooth/wired speaker, post-DSP and
  post-device-volume. It is a passive monitor copy: no links are moved and the
  signal chain is untouched.
* Worker: one `MeterWorker` `QThread` re-queries the target every attempt and
  compares it with the active capture, so changing the output device (or a
  Bluetooth drop/reconnect) restarts the capture on the right monitor even if
  the change lands during startup; the engine never blocks and the meter runs
  at ~45 Hz.
* Semantics: dBFS scale −60…0 with a −12 dB guide; the controller holds peaks
  (instant attack, 10 dB/s release); "Peak" shows the louder channel.
* Honesty rule learned live: a device at >100 % volume produces a clipped
  monitor copy, so the bar fills full-scale red and the numeric readout keeps
  the overshoot (e.g. "+3.5 dB") instead of hiding it. A full red bar means
  your device output is clipping — turn the *device* (not the app) down.

Verified on the live session (`MeterWorker` + real `pw-record` against
`bluez_output.…F2.1.monitor`): ~45 emissions/s, values follow the music, a
stopped capture drains the bars to zero, retries are back-off, no `pw-record`
process is left behind, `pw-link -l` is byte-identical before and after.

## Live verification (real session, real music)

At 23:16 local time the app was launched on the live Wayland session while
Chromium was playing to the Bluetooth JBL speaker:

* `wpctl` default output changed to `sonicfx_sink` (id 69, serial 1260);
* Chromium's stream moved to `sonicfx_sink:playback_FL/FR`;
* `sonicfx_out:output_FL/FR` was explicitly linked to the JBL's
  `playback_FL/FR`; `pw-link -l` shows exactly two output links and no loop;
* the JBL and Chromium nodes reported `running` at 1024/48 kHz;
* selecting **Movies / Cinema** in the GUI regenerated the chain and re-applied
  the default route; `~/.local/state/sonicfx/routing.json` kept the original
  JBL default;
* the autostart entry was written to
  `~/.config/autostart/sonicfx-ultimate.desktop`, launching
  `.venv/bin/python3 -m sonicfx.app`.

A separate silent wiring check used a private null sink and a test chain:
`Engine.start` and `Engine.apply` both routed FL/FR to the chosen sink, the
post-apply links were identical, the measured restart including re-wiring was
**255 ms**, and bypass removed the chain while the default sink stayed untouched.

## Routing safety

1. The default is switched **only after** the virtual sink is running and the
   playback side is explicitly wired to a real device.
2. Before switching, the previous default name is written atomically to
   `~/.local/state/sonicfx/routing.json` (mode 0600).
3. The recorded previous device is restored on Stop, routing-off, apply
   failure, window close and SIGINT/SIGTERM. If it has disappeared, the
   selected output device is used as a fallback.
4. A crash leaves the chain and default in place (audio keeps working); the
   next launch detects the dead owner PID, restores the old default, then
   starts again.
5. Only node names starting with `sonicfx` are accepted as the new default, and
   `pw-link` refuses SonicFX as its own playback target.
6. Audio is never sent to speakers by the automated tests: they use a private
   null sink and the preview uses a fake controller.

## Tests

`173` tests collected (172 pass, 1 live-skip); live PipeWire test remains opt-in.

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
SONICFX_LIVE=1 .venv/bin/python -m pytest tests/test_pipewire_live.py -v
```

New suites:

* `tests/test_wiring.py` — link-list parsing, sink discovery/self-exclusion,
  FL/FR port choice (prefers real `playback_*` inputs), loop guard.
* `tests/test_routing.py` — private atomic state, guard rails, restore,
  crash recovery and "another instance owns it".
* `tests/test_engine.py` — explicit autoconnect-off wiring, failure cleanup,
  no-op apply, re-wire on output change, bypass/re-enable.
* `tests/test_autostart.py` — XDG entry creation/removal, permissions, XDG path.

## Known limits (honest)

* DSP parameter edits still restart the filter-chain (measured 145 ms daemon
  restart; 255 ms including re-wiring). Edits are debounced, but a brief gap can
  be heard while fine-tuning. This is a PipeWire filter-chain limitation, not a
  simulated control.
* Only equalisation bands exposed by the chosen preset are editable; adding
  arbitrary bands and the dynamics/spatial blocks are later stages.
* There is still no LUFS/true-peak loudness meter or reconciliation loop yet;
  the Live Peak meter shows sample peaks of the output-device monitor only.
* If the GUI is force-killed, the chain keeps running (by design) and the
  default stays on SonicFX until the next launch's stale-state recovery or the
  user changes it manually.
* This is SonicFX's own DSP. It is not Dolby, not Dolby Atmos, and does not
  claim compatibility or certification.
