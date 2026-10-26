# SonicFX Ultimate - Windows installer
#
# Target: Windows 10/11 x64, 8 GB RAM, 4 GB free disk.
# Installs: Python 3.12 (if needed), Equalizer APO 1.4.2 (if needed), the
# SonicFX app in a local venv, a Startup launcher and Start Menu shortcut.
#
# Run from an elevated PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
#
# For a remote/SSH install (run as an administrator account):
#   $u='https://raw.githubusercontent.com/harshasamala9-lab/sonicfx-ultimate/main/scripts/install-windows.ps1'
#   $p="$env:TEMP\sonicfx-install.ps1"
#   Invoke-WebRequest $u -OutFile $p -UseBasicParsing
#   powershell -NoProfile -ExecutionPolicy Bypass -File $p -NoLaunch
#
# Equalizer APO is a separate GPL-2.0 project and is downloaded from its
# official SourceForge release. SonicFX only writes its configuration.

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'SonicFX'),
    [string]$RepoZipUrl = 'https://github.com/harshasamala9-lab/sonicfx-ultimate/archive/refs/heads/main.zip',
    [string]$LocalSource = '',
    [switch]$SkipEqualizerApo,
    [switch]$NoStartup,
    [switch]$NoLaunch,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
    # Older/newer hosts may already negotiate TLS 1.2+.
}

$MinimumRamGB = 8
$MinimumFreeGB = 4
$PythonVersion = '3.12.10'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$PythonSha256 = '67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb'
$ApoVersion = '1.4.2'
$ApoUrl = "https://sourceforge.net/projects/equalizerapo/files/$ApoVersion/EqualizerAPO-x64-$ApoVersion.exe/download"
$ApoSha256 = '7403be7427bbe1936a40dded082829b6e217fc4f5990fee5cba501f0ae055afa'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "   OK  $Message" -ForegroundColor Green
}

function Assert-FileHash([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "Checksum mismatch for $Path`nExpected: $Expected`nActual:   $actual"
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ApoConfigPath {
    foreach ($key in @(
        'HKLM:\SOFTWARE\EqualizerAPO',
        'HKLM:\SOFTWARE\WOW6432Node\EqualizerAPO'
    )) {
        try {
            $value = (Get-ItemProperty -Path $key -Name ConfigPath -ErrorAction Stop).ConfigPath
            if ($value -and (Test-Path -LiteralPath $value)) {
                return $value
            }
        } catch {
            # Try the next registry view / fallback path.
        }
    }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'EqualizerAPO\config'),
        (Join-Path ${env:ProgramFiles(x86)} 'EqualizerAPO\config')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Find-ApoInstallerOrFiles {
    $config = Get-ApoConfigPath
    if ($config) {
        return $config
    }
    $roots = @(
        (Join-Path $env:ProgramFiles 'EqualizerAPO'),
        (Join-Path ${env:ProgramFiles(x86)} 'EqualizerAPO')
    )
    foreach ($root in $roots) {
        if ($root -and (Test-Path -LiteralPath (Join-Path $root 'DeviceSelector.exe'))) {
            return (Join-Path $root 'config')
        }
        if ($root -and (Test-Path -LiteralPath (Join-Path $root 'Configurator.exe'))) {
            return (Join-Path $root 'config')
        }
    }
    return $null
}

function Find-Python {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        'C:\Python312\python.exe'
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $resolved = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                return $resolved.Trim()
            }
        } catch {
            # Fall through to installing Python.
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        try {
            $version = & $python.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version.Trim() -in @('3.11', '3.12', '3.13')) {
                return $python.Source
            }
        } catch {
            # Fall through to installing Python.
        }
    }
    return $null
}

function Install-Python {
    Write-Step "Installing Python $PythonVersion"
    $installer = Join-Path $env:TEMP "sonicfx-python-$PythonVersion-amd64.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $installer -UseBasicParsing
    Assert-FileHash -Path $installer -Expected $PythonSha256
    $arguments = @(
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=1',
        'Include_test=0',
        'Include_launcher=1',
        'Include_pip=1'
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Python installer exited with code $($process.ExitCode)"
    }
    $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python installed but $python was not found."
    }
    Write-Ok "Python $PythonVersion"
    return $python
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments,
        [string]$WorkingDirectory
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$Target,0"
    $shortcut.Save()
}

Write-Host 'SonicFX Ultimate - Windows installer' -ForegroundColor Magenta
Write-Host 'Free and local. Equalizer APO is the native DSP backend.'

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'SonicFX Ultimate requires 64-bit Windows 10 or 11.'
}
if (-not (Test-IsAdministrator)) {
    throw 'Run this installer from an elevated PowerShell (Run as Administrator), or connect over SSH as an administrator. Equalizer APO and the config ACL need admin rights.'
}

if (-not $SkipChecks) {
    Write-Step 'Checking the 8 GB RAM / 4 GB free-disk target'
    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($InstallDir))
    $drive = New-Object System.IO.DriveInfo($root)
    $freeGB = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    Write-Host "   RAM: $ramGB GB   Free disk: $freeGB GB   Install: $InstallDir"
    if ($ramGB -lt $MinimumRamGB) {
        throw "SonicFX needs at least $MinimumRamGB GB RAM for the supported low-spec profile (found $ramGB GB)."
    }
    if ($freeGB -lt $MinimumFreeGB) {
        throw "SonicFX needs at least $MinimumFreeGB GB free disk (found $freeGB GB)."
    }
}

Write-Step 'Preparing the source tree'
if ($LocalSource) {
    if (-not (Test-Path -LiteralPath (Join-Path $LocalSource 'pyproject.toml'))) {
        throw "LocalSource does not look like the SonicFX repository: $LocalSource"
    }
    $source = (Resolve-Path -LiteralPath $LocalSource).Path
} else {
    $zip = Join-Path $env:TEMP 'sonicfx-ultimate-main.zip'
    $extract = Join-Path $env:TEMP 'sonicfx-ultimate-src'
    Write-Host "   Downloading $RepoZipUrl"
    Invoke-WebRequest -Uri $RepoZipUrl -OutFile $zip -UseBasicParsing
    if (Test-Path -LiteralPath $extract) {
        Remove-Item -LiteralPath $extract -Recurse -Force
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $source = (Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1).FullName
    if (-not (Test-Path -LiteralPath (Join-Path $source 'pyproject.toml'))) {
        throw 'The downloaded archive did not contain pyproject.toml.'
    }
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
foreach ($item in @('src', 'scripts', 'docs', 'pyproject.toml', 'README.md', 'LICENSE')) {
    $sourceItem = Join-Path $source $item
    if (Test-Path -LiteralPath $sourceItem) {
        $targetItem = Join-Path $InstallDir $item
        if (Test-Path -LiteralPath $targetItem) {
            Remove-Item -LiteralPath $targetItem -Recurse -Force
        }
        Copy-Item -LiteralPath $sourceItem -Destination $targetItem -Recurse -Force
    }
}
Write-Ok "Source at $InstallDir"

Write-Step 'Preparing Python'
$python = Find-Python
if (-not $python) {
    $python = Install-Python
} else {
    Write-Ok "Using $python"
}
$venv = Join-Path $InstallDir '.venv'
if (Test-Path -LiteralPath $venv) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}
& $python -m venv $venv
$venvPython = Join-Path $venv 'Scripts\python.exe'
& $venvPython -m pip install --no-cache-dir --upgrade pip
& $venvPython -m pip install --no-cache-dir $InstallDir
Write-Ok 'SonicFX installed in its own venv (no global Python packages changed)'

if (-not $SkipEqualizerApo) {
    Write-Step "Installing Equalizer APO $ApoVersion (native C++ DSP)"
    $apoConfig = Find-ApoInstallerOrFiles
    if (-not $apoConfig) {
        $apoInstaller = Join-Path $env:TEMP "EqualizerAPO-x64-$ApoVersion.exe"
        Invoke-WebRequest -Uri $ApoUrl -OutFile $apoInstaller -UseBasicParsing
        Assert-FileHash -Path $apoInstaller -Expected $ApoSha256
        $process = Start-Process -FilePath $apoInstaller -ArgumentList '/S' -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "Equalizer APO installer exited with code $($process.ExitCode)."
        }
        Start-Sleep -Seconds 2
        $apoConfig = Find-ApoInstallerOrFiles
    }
    if (-not $apoConfig) {
        throw 'Equalizer APO was installed but its config folder was not found. Reboot, then rerun with -SkipEqualizerApo if the app should install without it.'
    }
    Write-Ok "Equalizer APO config: $apoConfig"

    # Give the current user write access to the config folder so the app can
    # update sonicfx.txt without a UAC prompt on every edit.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Write-Host "   Granting Modify on the config folder to $identity"
    & icacls.exe $apoConfig /grant "$($identity):(OI)(CI)M" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Could not grant config-folder write access. Run SonicFX as administrator if Apply fails.'
    } else {
        Write-Ok 'Equalizer APO config is user-writable'
    }
} else {
    Write-Warning 'Skipped Equalizer APO. The app will show a missing-backend message until it is installed.'
}

Write-Step 'Creating launchers'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'
$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$startupCmd = Join-Path $startup 'SonicFX Ultimate.cmd'
$settingsKey = 'HKCU:\Software\SonicFX\SonicFX Ultimate'
New-Item -Path $settingsKey -Force | Out-Null
if (-not $NoStartup) {
    New-Item -ItemType Directory -Path $startup -Force | Out-Null
    $launcher = "@echo off`r`nstart `"`" `"$pythonw`" -m sonicfx.app`r`n"
    Set-Content -LiteralPath $startupCmd -Value $launcher -Encoding ASCII -NoNewline
    # The app's own setting must agree with the file we just installed;
    # otherwise the first launch would remove it again.
    Set-ItemProperty -Path $settingsKey -Name 'login_autostart' -Value 'true'
    Write-Ok 'Startup launcher (toggle it off in the app)'
} else {
    Remove-Item -LiteralPath $startupCmd -Force -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $settingsKey -Name 'login_autostart' -Value 'false'
    Write-Ok 'Startup launcher disabled in the app settings'
}
$programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
New-Shortcut -Path (Join-Path $programs 'SonicFX Ultimate.lnk') -Target $pythonw -Arguments '-m sonicfx.app' -WorkingDirectory $InstallDir
$desktop = [Environment]::GetFolderPath('Desktop')
if ($desktop) {
    New-Shortcut -Path (Join-Path $desktop 'SonicFX Ultimate.lnk') -Target $pythonw -Arguments '-m sonicfx.app' -WorkingDirectory $InstallDir
}
Write-Ok 'Start Menu and desktop shortcuts'

Write-Step 'Done'
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Yellow
Write-Host '  1. Reboot if Equalizer APO asked for it.'
Write-Host '  2. Open SonicFX Ultimate and click "Open Equalizer APO".'
Write-Host '  3. Tick your headphones/speakers in Equalizer APO Device Selector.'
Write-Host '  4. Press Start in SonicFX, choose Cinema Immersion, and play something.'
Write-Host ''
Write-Host "App: $InstallDir"
Write-Host 'No ads, accounts, cloud calls or default-device switching.'
if (-not $NoLaunch) {
    Start-Process -FilePath $pythonw -ArgumentList '-m', 'sonicfx.app' -WorkingDirectory $InstallDir
} else {
    Write-Host 'Launch skipped (-NoLaunch), as requested for remote installs.'
}
