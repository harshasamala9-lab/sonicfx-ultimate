# Cinema Spatial (Linux native virtualiser)

**Cinema Spatial** is the Linux-only immersive preset. It is a two-channel
upmix/virtualisation stage implemented entirely in the PipeWire child daemon;
Python only designs the graph, resolves the output mode, generates the room
response and supervises the process. It is **not** Dolby Atmos, not an object
decoder and not a discrete 7.1.4 processor.

## Pick it in the app

1. Start SonicFX and select **Cinema Spatial**.
2. The **Cinema Spatial** card appears:
   * **Mode** — `Auto`, `Headphones · binaural`, or `Stereo speakers ·
     transaural`.
   * **Room** — small room, medium room, or large cinema (changes decay,
     predelay and synthetic-room wet level).
   * **Immersion** — 0–100 %, scaling the side/rear/height feeds and the
     speaker ambience.
3. Auto uses PipeWire device metadata: headphone/headset names or icons choose
   binaural; speaker, HDMI/DisplayPort, built-in analog or ≥4-channel devices
   choose speaker mode. Unknown stereo devices default to binaural. The manual
   override is always available.

The card is hidden for ordinary tonal presets and on Windows. On Windows the
Cinema Spatial preset still applies its tonal bands through Equalizer APO, but
the native spatial stage is not rendered.

### Headphones preset

The **Headphones** preset on Linux drives the same native binaural renderer,
but its mode is fixed to **Headphones · binaural**. That pinning is deliberate:
`Auto` can only infer the device class from PipeWire/WirePlumber metadata, and a
pair of headphones plugged into an analog-stereo or Bluetooth jack is often
reported as a generic speaker. With the Headphones preset selected, the HRTF
stage runs on every stereo output instead of waiting for the metadata to agree.
Room and Immersion still apply. The mode selector is disabled while that preset
is active; choose **Cinema Spatial** to return to Auto or speaker mode.

On Windows the Headphones preset applies only its tonal bands, like the other
shared presets. Equalizer APO has no SOFA HRTF stage.

## What the native graph does

### Binaural (headphones)

* 11 virtual feeds: front L/R/C, side L/R, rear L/R and four height feeds.
* Each feed is rendered by PipeWire's native SOFA `spatializer` using the
  measured **MIT KEMAR** HRTF (`/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa`).
* A band-limited centre (120 Hz–7 kHz) is extracted from the mid signal and
  subtracted from front L/R before being sent to the centre virtual speaker.
  This avoids three copies of the same dialogue and the resulting comb filter.
* Bass is managed once: front/side/rear feeds are high-passed, and a 4th-order
  LFE path is sent equally to both ears. The LFE is intentionally not
  HRTF-filtered.
* Side, rear and height feeds use different all-pass frequencies and delays
  instead of delayed clones, which reduces correlated "phasy" summation.
* A stereo room convolver adds a direct path, asymmetric lateral early
  reflections, and a damped common-mid tail with controlled side spread.

### Stereo speakers (transaural)

* Mid/side ambience extraction with three decorrelated taps and a common
  high-frequency air tap.
* Synthetic room with a deliberately drier tail than headphone mode (the real
  room already provides reverb).
* Simplified transaural crosstalk cancellation: a delayed, band-limited
  negative copy of the opposite channel with 1.18× makeup gain and a high-pass
  to keep bass mono-safe.
* Best results in the near-field sweet spot, with the speakers toed in and the
  listener on the centre line. It cannot create reliable rear/height images.

## Requirements

| Item | Why |
|---|---|
| PipeWire 1.6.x with `filter-chain`, `sofa`, `convolver`, `delay`, `biquad`, `mixer` | native real-time DSP |
| `libmysofa1` | SOFA reader and the MIT KEMAR dataset |
| `swh-plugins` | `fastLookaheadLimiter`; without it the graph uses fixed 0.55× headroom |
| Stereo output | the graph is stereo in/out; multichannel hardware needs a separate downmix |

Install on Ubuntu/Mint/Debian:

```bash
sudo apt-get install libmysofa1 swh-plugins
```

The main Linux installer (`scripts/install-linux.sh`) includes both packages.
Nothing is downloaded at runtime.

### Custom HRTF

The search order is:

1. `SONICFX_SOFA` (a `.sofa` file or a directory containing one),
2. the first `*.sofa` in `~/.local/share/sonicfx/hrtf/`,
3. `/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa`,
4. `/usr/share/libmysofa/default.sofa`.

A missing SOFA file does **not** silently fall back to a fake HRTF: binaural
mode is refused with an actionable error. Speaker mode does not need a SOFA
file.

## What it does not do

* No Dolby Atmos/object metadata decoding; no height or rear objects are
  recovered from a stereo mix.
* No discrete 5.1/7.1 input handling: PipeWire/the source has already folded
  the programme to stereo before this stage.
* No personalised HRTF, head tracking, or measured room correction.
* No claim that the virtual 7.1.4 layout equals twelve physical speakers.
* No cloud, account, telemetry or runtime download.

See [`feature-limitations.md`](feature-limitations.md) for the full statement.

## Live measurements (development machine)

Measured with a stereo test signal through the supervised child daemon, PipeWire
1.6.2, 48 kHz, MIT KEMAR SOFA. These are engineering checks, not a listening
panel:

| Mode | CPU (one core) | Centred signal L/R correlation | Centred side/mid | Out-of-phase side/mid |
|---|---:|---:|---:|---:|
| Binaural, no room | ~19–25 % | 0.735 | 0.391 | 4.42 |
| Binaural, tuned room | ~10–25 % | 0.639 | 0.470 | 2.73 |
| Speaker, tuned room | ~6–8 % | 0.839 | 0.296 | 3.30 |

The room deliberately widens the centred image a little; the centre extraction
and common-mid room tail keep it from collapsing into a diffuse cloud.

## Troubleshooting

| Symptom | Check |
|---|---|
| Binaural refused | `libmysofa1` installed? `ls /usr/share/libmysofa/*.sofa`; or set `SONICFX_SOFA` |
| Limiter warning | install `swh-plugins`; fixed headroom is still safe but less consistent |
| No rear/height on speakers | expected — use headphones for deliberate binaural cues |
| Thin bass on speakers | speaker mode high-passes the mains; use the preset's low shelf or a subwoofer, and check the output device |
| Too wide/phasey | lower **Immersion**, use a smaller room, or choose speaker mode |
| Auto picks the wrong mode | select Headphones or Speakers manually, or use the **Headphones** preset, which pins binaural; metadata is best-effort |
