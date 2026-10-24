# Free 4K Media Setup (movies + music)

Companion to SonicFX Ultimate: this makes the same laptop a smooth **4K movie
and music player** using only free/open-source software. It is independent of
the SonicFX audio engine and touches no system audio configuration.

Run once:

```bash
sudo scripts/install-media-4k.sh
```

## What this laptop actually supports

| Component | Value |
|---|---|
| GPU | Intel HD Graphics 620 (Kaby Lake, Gen 9.5) |
| Internal panel | 1366 × 768 |
| Hardware video decode | H.264, VC-1, MPEG-2, **HEVC 8/10-bit 4K**, **VP9 8-bit 4K** |
| No HW decode | AV1 (software only — 4K AV1 may stutter) |
| HDMI output | HDMI 1.4 ⇒ 4K @ 30 Hz |
| USB-C / DisplayPort | DP 1.2 ⇒ 4K @ 60 Hz (when the port supports DP alt mode) |

4K files play smoothly because they are decoded in fixed-function hardware and
downscaled to the 1366 × 768 panel; playback quality on the internal display
stays native.

## What the script installs (all free/open source)

* `intel-media-va-driver` — iHD VA-API driver (MIT), the correct driver for
  Gen 9.5; `libva2`, `libva-drm2`, `mesa-va-drivers` as support/fallback.
* `ffmpeg` — codecs and conversion.
* `mpv` + `celluloid` — player and GTK movie-player front end.
* `gstreamer1.0-vaapi`, `gstreamer1.0-libav`,
  `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`.
* `libva-utils` (provides `vainfo`) when available.
* Per-user config: `~/.config/mpv/mpv.conf` with `hwdec=vaapi`, GPU output,
  audio-master clock and a 512 MiB cache for network 4K.
* Firefox VA-API prefs appended to every profile's `user.js` (deb **and** snap
  profile locations are handled), with `.sonicfx-backup` copies.

Nothing is removed, nothing is made default without asking, and no proprietary
codec packs are installed. (`ubuntu-restricted-extras` is **not** needed for
H.264/HEVC/VP9 playback with mpv/ffmpeg and is deliberately left out.)

## Verify

```bash
vainfo | grep -E "Driver version|HEVC|VP9"
mpv --hwdec=vaapi '/path/to/movie.mkv'   # Ctrl+Shift+I shows decode stats
celluloid '/path/to/movie.mkv'
```

In Firefox: `about:support` → *Graphics* → look for VA-API entries; codec
support is listed under *Media*. Restart Firefox after the script (sign out and
back in once on Wayland for the environment to settle).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `vainfo` fails with iHD errors | Try the legacy driver: `LIBVA_DRIVER_NAME=i965 vainfo`; if that works, run players with `LIBVA_DRIVER_NAME=i965` |
| mpv falls back to software (`hwdec` warning) | `mpv --hwdec=vaapi --msg-level=vd=v` to see the reason; usually a missing profile (e.g. AV1) |
| Firefox still software-decodes | Confirm `media.ffmpeg.vaapi.enabled` in `about:config`; snap Firefox needs a session restart; check `about:support` for blocklist flags |
| 4K YouTube stutters in Firefox | Prefer 1440p on AV1-only videos; VP9 4K is hardware-decoded |
| External 4K TV limited to 30 Hz | HDMI 1.4 limit; use the USB-C/DP output for 4K@60 |

## Uninstall

```bash
sudo apt remove --purge mpv celluloid intel-media-va-driver libva-utils \
    gstreamer1.0-vaapi
rm -f ~/.config/mpv/mpv.conf ~/.config/mpv/mpv.conf.sonicfx-backup
# Firefox: remove the two user_pref lines from each profile's user.js
```
