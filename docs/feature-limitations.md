# Honest capability & limitation statement

SonicFX Ultimate is a free, local, open-source audio enhancement for Linux and
Windows. This page states exactly what it can and cannot do, so the UI/docs never
imply otherwise. (Requirement: no official-Dolby-equivalence claims, never fake
features.)

## Why there is no Dolby Atmos decoder

Dolby Atmos is not a format that can be added by storing more data or writing a
Python decoder. Object-audio playback needs a licensed Dolby decoder/renderer,
the original object metadata, and a suitable output layout; Linux/PipeWire does
not ship one, and a free local app cannot legally or technically substitute for
it. SonicFX therefore does **not** decode Atmos, does not claim Atmos
compatibility, and does not label any mode as Dolby.

What SonicFX does provide is a **stereo tonal Cinema Immersion preset** on the
native DSP backend. A future native PipeWire HRTF/multichannel virtualiser could
make stereo content feel wider on headphones or a multichannel device, but that
would still be an upmix/virtualisation effect, not an Atmos decoder.

## Backend split

| Platform | Native real-time engine | Controller role |
|---|---|---|
| Linux | PipeWire `filter-chain` biquad DSP | generates config, supervises the child daemon, routes explicitly |
| Windows 10/11 | Equalizer APO 1.4.x (native C++ APO) | generates `sonicfx.txt`, manages one safe `Include:` line, reads WASAPI loopback for the meter |

On Windows, Equalizer APO is a separate GPL-2.0 project. It is downloaded from
its official release by the installer; SonicFX does not bundle it and does not
put Python in the audio callback.

**Cinema Immersion** is the shared cinematic preset on both platforms. It is a
stereo tonal curve (bass shelf, dialogue presence, mud cut, high air). It is not
Dolby Cinema, Dolby Atmos, object audio, or a licensed Dolby implementation, and
it does not claim to recreate a Dolby cinema.

## Legitimate claims (verified against live PipeWire 1.6.2 DSP)

* System-wide real-time EQ (parametric/graphic) via PipeWire's in-process
  filter-chain biquad operators.
* Stereo widening and mid-side processing (SWH `matrixSpatialiser`, solver-based
  widening in filter-chain graphs) with configurable width; mono-compatibility
  is provided by a mixed/mono-safety switch applied before/after the wide stage.
* Digital reverb / room-ambience modelling on live audio (Calf Reverb LV2 or
  convolver with program-generated IRs) — this is an *effect on stereo content*,
  not a measurement-based room replication.
* Look-ahead limiting and compression for loudness/speaker safety.
* Convolver-based surround-virtualisation (Hesuvi/KEMAR simulator stacks) using
  freely-licensed impulse responses, or our own synthetic program IRs.
* Crossfeed / binaural-style headphone module (LADSPA bs2b + width/externalisation).
* Loudness metering (K-weighted, EBU-style) with optional slow loudness matching.

## Explicit limits (stated in-app on the relevant screens)

1. **No Dolby Atmos compatibility or equivalence.** Spatial virtualisation is a
   stereo/binaural post-processing effect; it does **not** reconstruct intended
   positional sound objects or mix-down dedicated surround programmes. No
   "object audio"/object-metadata rendering exists in this free pipeline.
2. **Virtual surround ≠ discrete multichannel.** 5.1/7.1 source downmix to two
   channels happens *before* SonicFX and is irrecoverable; our virtualisations
   restore the *cue pattern* (tuning inter-aural level/time differences) —
   an estimate, not channel restoration.
3. **HRTF note.** Convolver surround-virtualisation uses measurement-free
   synthetic program IRs unless the user supplies a freely-licensed HRTF/IR
   set (documented; some free/paid HRTF IR packs exist and can be dropped in).
   We will not claim "measured head-related transfer function" for synthetic IRs.
4. **Loudness ≠ quality.** The Dynamics screen explains target-vs-reference
   loudness (e.g., "Internet standard ≈ −14 LUFS for streaming") and that
   "louder" isn't "better". The limiter default prevents pumping.
5. **Bluetooth output.** When routed to a Bluetooth sink, the ultimate codec
   latency and fidelity are bounded by the codec, not by SonicFX; reported
   latency excludes codec internals.
6. **RNNoise microphone cleanup** requires an optional third-party LADSPA build
   (no Ubuntu package on this release). Absence is shown as "Not installed",
   never silently dropped.
7. **Multi-channel handling.** Upmix/downmix is only applied for labelled
   supported target layouts and with `FL/FR` guaranteed compatibility; we
   never silently discard channels (LFE routing is explicit on multichannel).
8. **"Spatial effect strength" extremes** are supported by the design, but the
   screen states explicit safe-defaults guidance to avoid mono-collapse.
9. **DSP edits restart the chain.** PipeWire filter-chain graph controls are not
   externally writable, so parameter changes are applied by atomic config
   regeneration and a supervised restart (measured 145 ms; 255 ms including
   re-wiring). Edits are debounced 300 ms; a brief gap can be audible while
   fine-tuning. No live control is simulated.
10. **Default routing is per desktop session and opt-in.** The GUI switch is on
    for this profile, but the previous default is always recorded first and
    restored on Stop, exit, failure or the next launch after a crash. If that
    device has disappeared, the selected output device is used. Force-killing
    the GUI leaves the already-running chain (and default) in place until
    recovery; the app never overwrites system PipeWire configuration.

## Windows-specific limits (Equalizer APO backend)

1. **No default-sink switching.** Windows audio is processed by Equalizer APO on
   the endpoints enabled in its Device Selector. SonicFX has no routing toggle
   there and never changes the Windows default output. This is intentional.
2. **Device Selector is manual.** Equalizer APO does not expose a stable
   command-line endpoint selector. After installing/rebooting, open
   **Open Equalizer APO** and tick the desired headphones/speakers/Bluetooth
   endpoint once.
3. **Reboot is normally required.** APO drivers are loaded by the Windows audio
   service. A config can exist before the reboot but remain silent.
4. **Equalizer APO config writes need ACLs.** The installer grants the current
   user Modify on the APO `config` folder. If the app is installed another way,
   Apply may require an elevated run.
5. **WASAPI loopback meter is best-effort.** The meter reads a loopback monitor
   stream via the optional `soundcard` package. Some exclusive-mode, unusual USB
   or Bluetooth drivers may not expose it. When unavailable the UI shows no
   invented level; DSP processing is independent of the meter.
6. **Windows physical validation is not in CI.** Config rendering, include
   add/remove safety, backups and the controller surface have automated tests.
   Actual Equalizer APO processing and loopback metering still need a Windows
   10/11 machine with real audio hardware. See `docs/windows.md` for the
   validation checklist; do not present the Windows build as "perfect" until
   that checklist is completed.
7. **Windows backend is EQ-focused.** Per-app routing, PipeWire virtual sinks,
   and Linux LADSPA/LV2 spatial modules are not available on Windows. The shared
   presets and fine-tune EQ are available; advanced spatial/dynamics features
   remain Linux-only until implemented against APO/VST or a native Windows DSP.
