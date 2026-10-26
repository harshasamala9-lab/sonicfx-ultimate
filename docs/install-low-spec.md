# Low-resource install: 8 GB RAM / 4 GB free disk

SonicFX Ultimate is designed to install on a normal 8 GB RAM laptop with at
least 4 GB of free disk. The installers refuse to start below those limits so a
low-memory machine does not fail halfway through a large Qt/Python install.

These are **free-disk** checks, not total-disk checks. Windows itself, the app
venv and the audio driver all share the same volume.

## Windows 10/11 x64

Local, elevated PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-windows.ps1
```

SSH, as an administrator (no GUI launch):

```powershell
$url = 'https://raw.githubusercontent.com/harshasamala9-lab/sonicfx-ultimate/main/scripts/install-windows.ps1'
$dst = "$env:TEMP\sonicfx-install.ps1"
Invoke-WebRequest $url -OutFile $dst -UseBasicParsing
powershell -NoProfile -ExecutionPolicy Bypass -File $dst -NoLaunch
```

What the installer checks and installs:

| Item | Value |
|---|---|
| RAM guard | 8 GB |
| Free-disk guard | 4 GB |
| Python | 3.12.10, per-user, only if needed |
| Native DSP | Equalizer APO 1.4.2 (SHA-256 verified) |
| Python packages | PySide6, NumPy, SciPy, `soundcard` (meter only) |
| Pip cache | Disabled (`--no-cache-dir`) and not kept |
| Default audio device | **Not changed** |

The Windows installer is documented in detail in [`windows.md`](windows.md).

## Linux (Ubuntu / Linux Mint / Debian)

From a checkout:

```bash
bash scripts/install-linux.sh
```

From a remote machine over SSH:

```bash
ssh user@host 'bash -s' < scripts/install-linux.sh
```

The script checks 8 GB RAM and 4 GB free disk, installs the minimal PipeWire/Qt
runtime packages plus `libmysofa1` (the MIT KEMAR SOFA HRTF dataset) and
`swh-plugins` (the look-ahead limiter), creates an isolated venv, installs the
app, and writes `~/.local/bin/sonicfx` plus a desktop entry. It does **not**
install the large optional LADSPA/LV2 plugin set and does not touch the default
sink.

Useful flags:

```bash
bash scripts/install-linux.sh --dev        # add pytest/ruff
bash scripts/install-linux.sh --no-apt     # dependencies already present
bash scripts/install-linux.sh --no-desktop # skip the .desktop entry
bash scripts/install-linux.sh --force      # bypass RAM/disk guards
bash scripts/install-linux.sh --dir PATH   # clone/install somewhere else
```

If the distro is older than Python 3.11, the script can install Python 3.12 from
the deadsnakes PPA on Ubuntu/Mint. On distributions where that PPA is not
appropriate, install Python 3.11+ manually and rerun with `--no-apt`.

## Disk footprint

Approximate steady-state sizes after installation:

| Component | Size |
|---|---|
| Python 3.12 runtime | 100 MB |
| PySide6/Qt wheel | 200-300 MB |
| NumPy + SciPy | 80-150 MB |
| SonicFX source + venv metadata | 10-30 MB |
| Equalizer APO (Windows) | ~12 MB |
| **Total** | **~0.5-0.8 GB** |

The 4 GB guard leaves headroom for downloads, the Windows component store and
temporary extraction. It is not a claim that every 4 GB machine has 4 GB free
after the operating system.

## SSH notes

* On Windows, an SSH session may not have access to the interactive desktop.
  Install with `-NoLaunch`, then sign in locally and open SonicFX.
* Equalizer APO is a system audio driver. The SSH account must be an
  administrator, and a reboot is normally required.
* On Linux, the user must have a PipeWire session at the desktop. Installing
  over SSH starts no audio session; log in locally afterward.
* No secrets or tokens are needed. The repository and both installers are
  public.

## Honest limits

The 8 GB / 4 GB figures cover installation and normal stereo EQ use. They do
not make the app a Dolby Cinema/Atmos processor. Cinema Immersion is a stereo
tonal curve (bass, dialogue presence, air). Cinema Spatial is a two-channel
upmix/virtualiser: HRTF binaural on headphones or transaural processing on
stereo speakers. Neither can reconstruct object audio or a discrete surround
mix. See [`feature-limitations.md`](feature-limitations.md) and
[`cinema-spatial.md`](cinema-spatial.md).
