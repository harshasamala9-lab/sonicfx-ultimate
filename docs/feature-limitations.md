# Honest capability & limitation statement

SonicFX Ultimate is a free, local, open-source audio enhancement for Linux.
This page states exactly what it can and cannot do, so the UI/docs never imply
otherwise. (Requirement: no official-Dolby-equivalence claims, never fake features.)

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
