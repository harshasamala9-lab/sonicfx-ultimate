# Windows guide

SonicFX Ultimate runs on Windows 10/11 x64 with **Equalizer APO** as the native
real-time DSP engine. The SonicFX window is still a Qt controller: it writes a
small Equalizer APO configuration file and never processes audio in Python.

This is a system-wide, stereo tonal enhancement. It is **not** Dolby Cinema,
Dolby Atmos, object audio, or a licensed Dolby product. The shared cinematic
preset is named **Cinema Immersion** for that reason. The Linux-only **Cinema
Spatial** HRTF/transaural stage and the **Headphones** HRTF stage are not
available through Equalizer APO; on Windows those presets apply their tonal
curves only.

![SonicFX Ultimate on Windows](ui-preview-windows.png)

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 or Windows 11, 64-bit |
| RAM | 8 GB minimum (installer refuses below 8 GB) |
| Free disk | 4 GB minimum (installer refuses below 4 GB) |
| Privileges | Administrator once, for Equalizer APO and its config ACL |
| Output | Any Windows render endpoint that Equalizer APO supports |
| Reboot | Usually required after the Equalizer APO driver install |

Windows itself, the app's venv, PySide6, NumPy/SciPy and Python use roughly
0.7-1.2 GB of that free space. Equalizer APO is about 12 MB. Pip caches are
disabled during install.

## Install

### Local PowerShell (recommended)

Download the repository, open **PowerShell as Administrator**, then:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-windows.ps1
```

The installer:

1. Checks 64-bit Windows, 8 GB RAM and 4 GB free disk.
2. Installs Python 3.12.10 if no suitable Python exists.
3. Downloads and SHA-256 verifies Equalizer APO 1.4.2 from SourceForge.
4. Creates `%LOCALAPPDATA%\SonicFX\.venv` and installs SonicFX there.
5. Grants the current user **Modify** on Equalizer APO's `config` folder only,
   so the app can update `sonicfx.txt` without a UAC prompt on every edit.
6. Adds a Startup launcher, Start Menu shortcut and desktop shortcut.
7. Starts SonicFX (unless `-NoLaunch` is passed).

### Over SSH

Connect as an administrator account and run the installer without launching the
GUI (an SSH session may not own the interactive desktop):

```powershell
$url = 'https://raw.githubusercontent.com/harshasamala9-lab/sonicfx-ultimate/main/scripts/install-windows.ps1'
$dst = "$env:TEMP\sonicfx-install.ps1"
Invoke-WebRequest $url -OutFile $dst -UseBasicParsing
powershell -NoProfile -ExecutionPolicy Bypass -File $dst -NoLaunch
```

Then sign in to the interactive desktop once to complete the Equalizer APO
Device Selector step and launch SonicFX.

### Installer switches

| Switch | Meaning |
|---|---|
| `-InstallDir PATH` | Install location; default `%LOCALAPPDATA%\SonicFX` |
| `-LocalSource PATH` | Install from a local checkout instead of the GitHub zip |
| `-SkipEqualizerApo` | Install the controller only; useful for diagnostics |
| `-NoStartup` | Do not add the Startup launcher (also disables it in the app's saved settings) |
| `-NoLaunch` | Do not launch the GUI at the end (recommended over SSH) |
| `-SkipChecks` | Bypass the RAM/disk guard (not recommended) |

## First run: enable your output

Equalizer APO applies processing to the endpoints selected in its own **Device
Selector**. SonicFX does not silently change the Windows default device.

1. Reboot if Equalizer APO asked for it.
2. Open **SonicFX Ultimate**.
3. Click **Open Equalizer APO**.
4. Tick your headphones/speakers (and Bluetooth output, if desired).
5. Return to SonicFX, click **Start**, and select **Cinema Immersion**.
6. Play something and confirm the peak meter moves.

If the meter is unavailable, the DSP can still work. The meter is an optional
WASAPI loopback monitor, not part of the audio path. Its accuracy depends on the
endpoint and the `soundcard` package; it has not been validated on every
Bluetooth stack.

## How the backend works

* `EqualizerApo` finds `ConfigPath` under
  `HKLM\SOFTWARE\EqualizerAPO` (with Program Files fallbacks).
* The generated graph is written atomically to `config\sonicfx.txt`.
* The user's `config.txt` is preserved. SonicFX adds exactly one
  `Include: sonicfx.txt` line, and makes a one-time
  `config.txt.sonicfx-backup` copy before the first change.
* **Stop** removes the include and writes a disabled marker to `sonicfx.txt`,
  leaving the user's own EQ lines untouched.
* Filter syntax follows the official Equalizer APO configuration reference:
  `Preamp`, `PK`, `LS`, `HS`, `HPQ`, `LPQ`, `BP`, `NO`, `AP`.
* A headroom preamp is generated automatically: the largest positive band gain
  becomes a negative preamp (for example `+4.5 dB` → `Preamp: -4.5 dB`).

> **Per-device configs:** Equalizer APO's Device Selector can assign a custom
> configuration file to an individual output. If you enabled that, the global
> `config.txt` include is bypassed for that device. Either remove the per-device
> custom config or add `Include: sonicfx.txt` to that device's own file.

Nothing in this path uses Python for sample processing. The C++ Equalizer APO
APO runs inside the Windows audio engine.

## Known Windows limits

1. **No default-device switching or per-app routing.** Equalizer APO operates
   on the endpoints selected in its Device Selector. This is deliberate: the app
   never changes the default output behind your back.
2. **Device Selector is a manual one-time step.** Equalizer APO does not provide
   a stable command-line interface for selecting render endpoints, so the
   installer opens the app but cannot tick devices for you.
3. **Reboot can be required.** APO drivers are loaded by the Windows audio
   service. Until the reboot, processing may be silent even though the config is
   active.
4. **Meter is loopback-based.** It reflects the endpoint's monitor stream. Some
   exclusive-mode or unusual Bluetooth/USB drivers may not expose loopback.
5. **Cinema Immersion is EQ, not object audio.** It cannot recover height or
   surround channels that were already downmixed to stereo. No Dolby/Atmos
   compatibility is claimed.
6. **Windows hardware validation is still required.** The config layer, include
   safety and controller are covered by automated tests on Linux, but this repo's
   CI does not have a Windows audio device. Use the checklist below on a real
   Windows 10/11 machine before treating the build as release-proven.

## Validation checklist (real Windows machine)

- [ ] Installer completes with 8 GB RAM / 4 GB free disk.
- [ ] `config\config.txt` contains exactly one `Include: sonicfx.txt`.
- [ ] `config\config.txt.sonicfx-backup` contains the pre-SonicFX config.
- [ ] Starting the app creates filters in `config\sonicfx.txt`.
- [ ] Toggling Start/Stop adds/removes the include without touching user EQ.
- [ ] Cinema Immersion produces audible bass/presence change on speakers.
- [ ] The peak meter moves for speakers, wired headphones and Bluetooth.
- [ ] Closing the app disables the include (or leaves the expected state).
- [ ] Reboot preserves the Startup launcher and the app re-enables processing.
- [ ] Uninstall steps restore the original config.

## Troubleshooting

| Symptom | Check |
|---|---|
| "Equalizer APO was not found" | Reboot, then rerun the installer without `-SkipEqualizerApo` |
| Apply says access denied | Run the installer as Administrator again so it can grant config ACLs |
| No sound change | Open Device Selector, tick the endpoint, then restart playback; check that the device is not using a per-device custom config that omits the SonicFX include |
| Meter stays at −∞ | Check the selected meter output; try a non-Bluetooth endpoint; verify `soundcard` installed |
| Crackling/clipping | Lower Windows volume; Cinema Immersion adds +4.5 dB bass and may exceed headroom on small speakers |
| App won't start | Check `%LOCALAPPDATA%\SonicFX\logs\sonicfx.log` |

## Uninstall

1. In SonicFX, press **Stop** so the include is removed.
2. Close the app.
3. Delete `%LOCALAPPDATA%\SonicFX`.
4. Remove the Startup entry
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SonicFX Ultimate.cmd`
   and the Start Menu/desktop shortcuts.
5. Optionally uninstall **Equalizer APO** and reboot. Restore
   `config.txt.sonicfx-backup` only if you want the pre-install config back.

## Privacy

No accounts, ads, telemetry, cloud APIs or paid services. The installer makes
only the documented Python/Equalizer APO downloads. At runtime the app is local;
it reads/writes Equalizer APO's config and, optionally, the WASAPI loopback
stream for the meter.
