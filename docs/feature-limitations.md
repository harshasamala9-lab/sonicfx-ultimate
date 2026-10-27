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

What SonicFX does provide on Linux is **Cinema Spatial**, a native PipeWire
virtualiser. On headphones it renders a virtual 7.1.4-style ring (front, side,
rear and four height feeds) through a measured SOFA HRTF and a synthetic room
response. On stereo speakers it uses transaural width, decorrelated ambience,
early reflections and a simplified crosstalk-cancellation matrix. It is an
upmix/virtualisation effect, not an object-audio decoder, and it cannot recover
channels that were already folded into a stereo mix.

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

**Cinema Spatial** is the Linux-only native virtualiser. It is honest about
being a two-channel effect:

| Mode | What it does | What it does not do |
|---|---|---|
| Headphones · binaural | 11 virtual speaker feeds (7.1.4-style) convolved with a measured MIT KEMAR SOFA HRTF; band-limited centre extraction; bass management; decorrelated rear/height feeds; synthetic room | Personalised HRTF, head tracking, object rendering, discrete 7.1.4 input |
| Stereo speakers · transaural | Mid/side ambience, lateral early reflections, synthetic room, and a delayed band-limited negative crossfeed with makeup gain | True rear/height speakers, a wide sweet spot, or object audio |
| Auto | Picks binaural for headphone/headset metadata and speaker mode for speakers/HDMI | A guarantee when PipeWire metadata is wrong; manual override is always available |

The 7.1.4 label describes the **virtual layout**, not twelve physical outputs
and not an Atmos bed. The room response is synthetic, not a measured venue.

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
* Native SOFA HRTF binaural virtualisation with the freely licensed MIT KEMAR
  dataset (`libmysofa1`), 11 virtual speaker feeds, band-limited centre
  extraction, bass management and all-pass decorrelation.
* Convolver-based room/ambience processing using our own deterministic synthetic
  stereo IRs (explicit direct path, asymmetric early reflections, common-mid
  tail with controlled side spread).
* Speaker transaural virtualisation: mid/side ambience, lateral early
  reflections, synthetic room and a simplified crosstalk-cancellation matrix.
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
3. **HRTF note.** Binaural mode uses the **measured MIT KEMAR** SOFA dataset
   shipped by `libmysofa1` (or a user-supplied `.sofa` file / `SONICFX_SOFA`).
   It is a measured dummy-head HRTF, not a personalised measurement of the
   listener's ears. Fit varies by person; manual speaker mode is provided when
   binaural cues do not work for a listener. If no SOFA file is installed,
   binaural mode is refused rather than silently replaced with a fake HRTF.
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
11. **Speaker mode has a sweet spot.** The negative crossfeed is a simplified
    transaural inverse, not a measured room/head cancellation filter. It widens
    the image near the centre line and can reduce bass in mono; it cannot put
    sound reliably behind or above a listener. Headphones are the only mode
    with deliberate rear/height HRTF cues.
12. **The room is synthetic.** The stereo room IR is generated deterministically
    from a seeded model (direct path, asymmetric early taps, damped tail). It is
    not a measured cinema or a convolution of the listener's actual room, and
    loudspeaker mode deliberately uses a drier tail to avoid double reverb.
13. **Limiter fallback.** `fastLookaheadLimiter` is provided by `swh-plugins`.
    When it is absent, the graph uses fixed headroom (0.55×) instead of silently
    clipping; the UI says so. The fallback is safe but cannot adapt to level.
14. **CPU and latency.** Binaural mode runs 11 SOFA spatialisers, a stereo
    convolver and two limiter channels in the PipeWire audio thread. Measured
    on the development machine at about 10–25 % of one core (speaker mode about
    6–8 %), but slower CPUs and Bluetooth codecs add their own load/latency.
    DSP edits restart the chain; there is no live control of the native graph.

## Wave Studio (visualisation) limits

1. **Decorative, not metrology.** The six Wave Studio modes are audio-reactive
   artwork driven by FFT magnitude bins, RMS band energy and a transient
   indicator. They are *not* measurement instruments: no SPL/ISO compliance, no
   calibrated spectrum, no LUFS display, no RTA certification. The live stereo
   Peak meter remains the only level readout.
2. **Light Wave is wavelength-*inspired*.** Colours interpolate across the
   visible-spectrum hue range for aesthetics. Light Wave performs no optical
   measurement whatsoever; hue and frequency are only visually paired.
3. **"3D Cinema Space" is a perspective drawing.** Depth-layered waveform lines
   are rendered with a 2-D painter; they are not spatial audio positions,
   measured HRTF geometry, or room acoustics measurements.
4. **Second passive capture.** On Linux the analysis worker opens a *separate*
   `pw-record` on the selected output's `*.monitor` node, next to the Peak
   meter's tap. Recording two monitor streams costs a small amount of extra
   CPU/IO. It is plain `--raw -` float32 reading; the realtime audio thread is
   never blocked by Python analysis, which runs in a `QThread` on ~25 Hz
   bounded snapshots. Spectrogram history is capped (160 columns) and every
   emitted array is fixed-size.
5. **Audio reactivity is Linux-only for now.** On Windows the same window
   shows the Wave Studio card, but the analysis worker has no capture target
   there (monitor taps are PipeWire `*.monitor` nodes and `pw-record` does not
   exist on Windows); the card renders its slow idle-motion only. Wiring the
   analyser to the existing WASAPI loopback meter would be the follow-up work.
   Every control still functions; the mode captions never imply live data that
   is not being read.
6. **Silence is animated gently.** When playback is silent the visualiser keeps
   a slow idle motion by design so the studio does not look frozen; it is not
   pretending to audio in that state.
7. **Frame-rate target, not guarantee.** 60/30/15 FPS and reduced-motion are
   user settings. Smooth 60 FPS depends on the GPU/compositor; reduced
   resource usage (FPS Off, reduced motion) is the supported low-spec path.

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
   SOFA HRTF spatialisers, room convolvers and Linux LADSPA/LV2 modules are not
   available on Windows. The shared presets and fine-tune EQ are available;
   **Cinema Spatial is Linux-only**, and the Windows Cinema Spatial preset is a
   useful tonal curve only (the `spatial` flag is ignored by the APO renderer).
   Advanced spatial/dynamics features remain Linux-only until implemented
   against APO/VST or a native Windows DSP.
