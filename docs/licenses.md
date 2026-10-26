# License inventory (runtime + build)

SonicFX Ultimate itself: **MIT** (`LICENSE`).

Linux runtime dependencies are obtained from Ubuntu repository packages (no
pushing/patching of third-party artefacts into the system audio config).
Windows obtains Python and Equalizer APO from their official installers; no
third-party binary is bundled in this repository.

| Component | License | Why/runtime role | Package / source |
|---|---|---|---|
| PipeWire 1.6.2 | MIT/LGPL-2.1 | real-time graph + hosting of DSP plugins in audio thread | `pipewire`, `pipewire-pulse`, `wireplumber` |
| libpipewire-module-filter-chain | MIT (part of PipeWire) | the C DSP chains we generate configs/host | `pipewire` |
| SWH plugins (Steve Harris LADSPA) | GPL-2.0+ | stereo widening (`matrixSpatialiser`), look-ahead limiter for Cinema Spatial, Dyson compressor | `swh-plugins` |
| libmysofa / MIT KEMAR SOFA | BSD-3-Clause (library) + MIT KEMAR dataset terms | native SOFA HRTF reader and measured binaural dataset | `libmysofa1` |
| Calf Studio Gear (LV2/LADSPA) | LGPL-2.1+ | compressor, look-ahead limiter, reverb, multiband, gate, de-esser | `calf-plugins` |
| Zam-plugins (LV2) | GPL-3.0+ | ZaMaximX2/comp limiter, gate (fallbacks) | `zam-plugins` |
| Bauer bs2b LADSPA | GPL-2.0+ & MIT | headphone crossfeed algorithm | `bs2b-ladspa` |
| amb-plugins | GPL-2.0+ | ambisonic helper experiments (optional) | `amb-plugins` |
| pulseaudio-utils (`pactl`) | LGPL-2.1+ | diagnostics convenience | `pulseaudio-utils` |
| Equalizer APO 1.4.2 | GPL-2.0 | Windows real-time APO/DSP backend (separate native process) | official SourceForge installer |
| soundcard | BSD-3-Clause | optional WASAPI loopback peak meter on Windows | pip wheel (`soundcard`) |
| PySide6 6.12 | LGPL-3.0 | Qt6 desktop GUI bindings | pip wheel (`PySide6`) |
| NumPy | BSD-3-Clause | offline design/validation math (not audio path) | pip |
| SciPy | MIT(mostly) | offline designs / test reference math | pip |
| pytest (+pytest-qt etc.) | MIT | test tooling only | pip |
| Python | PSF-2.0 | application VM | `python3` / python.org installer |
| RNNoise (optional opt-in build) | BSD-3-Clause | FCC/DOR metadata? — training set (speech model levels), optional | source build (see limits doc) |

Notes:

* There is no bundling of *proprietary* DSP code; no Dolby software or branding.
* Because running processes host GPL-2.0+ LADSPA plugins in the same address
  space (PipeWire process), mixing is treated as a link event; we do not copy
  plugin sources into this project and simply document the freestanding
  dependency chain, as allowed for normal apt-installed components.
* Equalizer APO is a separate GPL-2.0 program installed by the user. SonicFX
  talks to it only by writing its documented configuration file; it is not
  linked into the SonicFX Python process.
* pip-installed wheels are fetched once at install; the Linux installer may
  cache them, while the Windows installer uses `--no-cache-dir` to keep the
  4 GB low-spec target. The application has **no mandatory network access at
  runtime**.
