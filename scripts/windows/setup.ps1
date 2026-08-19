<#
.SYNOPSIS
    Set up the MilSim / OpenRA-RL Python environment on Windows.

.DESCRIPTION
    Checks for a suitable Python (3.10+), creates a virtual environment,
    installs the openra-rl package (which carries every dependency milsim
    needs), makes the repo-root `milsim` package importable from any working
    directory, and then verifies the install by actually importing every
    module and loading a scenario file.

    The script never calls Activate.ps1, so it works under the default
    Restricted / RemoteSigned execution policy without changing it.

.PARAMETER Python
    Explicit Python interpreter to build the venv from, e.g.
    "C:\Python312\python.exe". Default: auto-detect (py -3, python, python3).

.PARAMETER VenvPath
    Where to create the virtual environment. Default: <repo>\.venv

.PARAMETER Recreate
    Delete an existing virtual environment before creating it.

.PARAMETER WithTorch
    Also install PyTorch. Optional: milsim\cnn.py falls back to pure-Python
    convolution kernels when torch is missing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1

.EXAMPLE
    pwsh -File scripts\windows\setup.ps1 -Recreate -WithTorch

.NOTES
    Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#>

[CmdletBinding()]
param(
    [string] $Python = '',
    [string] $VenvPath = '',
    [switch] $Recreate,
    [switch] $WithTorch
)

# NOTE: deliberately NOT 'Stop'. In Windows PowerShell 5.1, $ErrorActionPreference
# = 'Stop' turns stderr output from native commands (pip is chatty on stderr)
# into terminating NativeCommandError exceptions. Every external call below is
# checked explicitly via $LASTEXITCODE instead, and the few cmdlets that must
# abort carry -ErrorAction Stop.
$ErrorActionPreference = 'Continue'

$MinMajor = 3
$MinMinor = 10

# ---------------------------------------------------------------- helpers ---

function Write-Step { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string] $Message) Write-Host "    OK   $Message" -ForegroundColor Green }
function Write-Warn { param([string] $Message) Write-Host "    WARN $Message" -ForegroundColor Yellow }
function Write-Err  { param([string] $Message) Write-Host "    FAIL $Message" -ForegroundColor Red }

function Write-Utf8File {
    # Deterministic, BOM-free UTF-8. Set-Content/Out-File differ between 5.1
    # (writes a BOM) and 7 (does not), and a BOM breaks .pth parsing.
    param([string] $Path, [string] $Content)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function New-TempPythonFile {
    param([string] $Name, [string] $Content)
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) 'milsim-setup'
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
    }
    $path = Join-Path $dir $Name
    Write-Utf8File -Path $path -Content $Content
    return $path
}

function Test-PythonCandidate {
    # Returns $null if unusable, else a hashtable with Exe / Prefix / Version.
    param([string] $Exe, [string[]] $Prefix, [string] $ProbeScript)

    $command = Get-Command -Name $Exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { return $null }

    # The Microsoft Store "App Execution Alias" is a stub that is not a real
    # interpreter; it either exits 9009 or pops open the Store.
    if ($command.Path -and $command.Path -like '*\WindowsApps\*') {
        Write-Warn "Skipping '$Exe' - it is the Microsoft Store alias stub ($($command.Path))"
        return $null
    }

    $argList = @()
    if ($Prefix) { $argList += $Prefix }
    $argList += $ProbeScript

    $global:LASTEXITCODE = 0
    $output = & $Exe @argList 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    if (-not $output) { return $null }

    $lines = @($output)
    if ($lines.Count -lt 3) { return $null }

    $major = 0; $minor = 0
    if (-not [int]::TryParse($lines[0].Trim(), [ref] $major)) { return $null }
    if (-not [int]::TryParse($lines[1].Trim(), [ref] $minor)) { return $null }

    return @{
        Exe     = $Exe
        Prefix  = $Prefix
        Major   = $major
        Minor   = $minor
        Path    = $lines[2].Trim()
        Display = "$major.$minor"
    }
}

# ------------------------------------------------------------ repo layout ---

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$OpenRaRlDir = Join-Path $RepoRoot 'openra-rl'
$MilsimDir = Join-Path $RepoRoot 'milsim'

Write-Host ''
Write-Host 'MilSim - Windows setup' -ForegroundColor White
Write-Host "Repository: $RepoRoot"
Write-Host "PowerShell: $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
Write-Host ''

$OpenRaRlUrl = 'https://github.com/yxc20089/OpenRA-RL.git'

Write-Step 'Checking repository layout'
$required = @(
    (Join-Path $MilsimDir '__main__.py'),
    (Join-Path $OpenRaRlDir 'pyproject.toml')
)
foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath $item)) {
        Write-Err "Missing $item"
        Write-Host ''
        Write-Host 'openra-rl is a separate checkout, not a submodule of this repo.' -ForegroundColor Yellow
        Write-Host 'From the repository root, fetch it alongside milsim:' -ForegroundColor Yellow
        Write-Host ('  git clone --recurse-submodules ' + $OpenRaRlUrl + ' openra-rl') -ForegroundColor Yellow
        Write-Host '  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1' -ForegroundColor Yellow
        exit 1
    }
}
Write-Ok 'milsim\ and openra-rl\ found'

if ($VenvPath -eq '') { $VenvPath = Join-Path $RepoRoot '.venv' }

# -------------------------------------------------------- find interpreter ---

Write-Step "Looking for Python $MinMajor.$MinMinor or newer"

$probe = New-TempPythonFile -Name 'probe_version.py' -Content @'
import sys
print(sys.version_info[0])
print(sys.version_info[1])
print(sys.executable)
'@

$candidates = @()
if ($Python -ne '') {
    $candidates += ,@{ Exe = $Python; Prefix = @() }
} else {
    $candidates += ,@{ Exe = 'py';      Prefix = @('-3') }
    $candidates += ,@{ Exe = 'python';  Prefix = @() }
    $candidates += ,@{ Exe = 'python3'; Prefix = @() }
}

$chosen = $null
$tooOld = @()
foreach ($candidate in $candidates) {
    $info = Test-PythonCandidate -Exe $candidate.Exe -Prefix $candidate.Prefix -ProbeScript $probe
    if ($null -eq $info) { continue }
    if ($info.Major -lt $MinMajor -or ($info.Major -eq $MinMajor -and $info.Minor -lt $MinMinor)) {
        $tooOld += "$($candidate.Exe) -> $($info.Display) at $($info.Path)"
        continue
    }
    $chosen = $info
    break
}

if ($null -eq $chosen) {
    Write-Err "No Python $MinMajor.$MinMinor+ interpreter found."
    foreach ($old in $tooOld) { Write-Warn "Too old: $old" }
    Write-Host ''
    Write-Host 'Install Python from https://www.python.org/downloads/windows/' -ForegroundColor Yellow
    Write-Host 'and tick "Add python.exe to PATH" in the installer, then re-run this script.' -ForegroundColor Yellow
    Write-Host 'Do not use the Microsoft Store stub - it cannot create usable venvs here.' -ForegroundColor Yellow
    exit 1
}

Write-Ok "Python $($chosen.Display) at $($chosen.Path)"

# -------------------------------------------------------------------- venv ---

if ($Recreate -and (Test-Path -LiteralPath $VenvPath)) {
    Write-Step "Removing existing environment $VenvPath"
    Remove-Item -LiteralPath $VenvPath -Recurse -Force -ErrorAction Stop
}

$VenvPython = Join-Path (Join-Path $VenvPath 'Scripts') 'python.exe'
$VenvPythonPosix = Join-Path (Join-Path $VenvPath 'bin') 'python'

if (Test-Path -LiteralPath $VenvPython) {
    Write-Step "Reusing virtual environment $VenvPath"
} elseif (Test-Path -LiteralPath $VenvPythonPosix) {
    Write-Step "Reusing virtual environment $VenvPath"
    $VenvPython = $VenvPythonPosix
} else {
    Write-Step "Creating virtual environment $VenvPath"
    $argList = @()
    if ($chosen.Prefix) { $argList += $chosen.Prefix }
    $argList += @('-m', 'venv', $VenvPath)

    $global:LASTEXITCODE = 0
    & $chosen.Exe @argList
    if ($LASTEXITCODE -ne 0) {
        Write-Err "venv creation failed (exit $LASTEXITCODE)."
        Write-Host 'If you see "ensurepip is not available", reinstall Python with the' -ForegroundColor Yellow
        Write-Host '"pip" optional feature enabled.' -ForegroundColor Yellow
        exit 1
    }

    if (Test-Path -LiteralPath (Join-Path (Join-Path $VenvPath 'Scripts') 'python.exe')) {
        $VenvPython = Join-Path (Join-Path $VenvPath 'Scripts') 'python.exe'
    } elseif (Test-Path -LiteralPath $VenvPythonPosix) {
        $VenvPython = $VenvPythonPosix
    } else {
        Write-Err "venv created but no interpreter found under $VenvPath"
        exit 1
    }
}
Write-Ok "Interpreter: $VenvPython"

# UTF-8 mode for every child Python: milsim\scenario.py opens scenario YAML
# without an explicit encoding, and those files contain U+2014 em dashes.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# ---------------------------------------------------------------- installs ---

Write-Step 'Upgrading pip, setuptools and wheel'
$global:LASTEXITCODE = 0
& $VenvPython -m pip install --upgrade --disable-pip-version-check pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip upgrade failed (exit $LASTEXITCODE). Check your network or proxy settings."
    exit 1
}
Write-Ok 'pip toolchain up to date'

Write-Step 'Installing openra-rl (editable, with dev extras)'
$installTarget = "$OpenRaRlDir[dev]"
$global:LASTEXITCODE = 0
& $VenvPython -m pip install --disable-pip-version-check -e $installTarget
if ($LASTEXITCODE -ne 0) {
    Write-Warn 'Install with [dev] extras failed; retrying without extras.'
    $global:LASTEXITCODE = 0
    & $VenvPython -m pip install --disable-pip-version-check -e $OpenRaRlDir
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pip install failed (exit $LASTEXITCODE)."
        Write-Host 'Common causes on Windows:' -ForegroundColor Yellow
        Write-Host '  * No network access to PyPI (the editable install needs hatchling).' -ForegroundColor Yellow
        Write-Host '  * A wheel had to be built from source - install the "Desktop development' -ForegroundColor Yellow
        Write-Host '    with C++" workload from the Visual Studio Build Tools.' -ForegroundColor Yellow
        exit 1
    }
}
Write-Ok 'openra-rl installed'

if ($WithTorch) {
    Write-Step 'Installing PyTorch (optional)'
    $global:LASTEXITCODE = 0
    & $VenvPython -m pip install --disable-pip-version-check torch
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'PyTorch install failed. milsim\cnn.py will use its pure-Python fallback.'
    } else {
        Write-Ok 'PyTorch installed'
    }
}

# ------------------------------------------------- make milsim importable ---

# `milsim` lives at the repository root, has no __init__.py and no packaging
# metadata, so pip cannot install it. Drop a .pth into site-packages so that
# `import milsim` works regardless of the current directory.
Write-Step 'Registering the repo root on sys.path (for `import milsim`)'

$pthScript = New-TempPythonFile -Name 'write_pth.py' -Content @'
import pathlib
import sys
import sysconfig

repo_root = pathlib.Path(sys.argv[1]).resolve()
site_dir = pathlib.Path(sysconfig.get_paths()["purelib"])
site_dir.mkdir(parents=True, exist_ok=True)
pth = site_dir / "milsim_repo_root.pth"
pth.write_text(str(repo_root) + "\n", encoding="utf-8")
print(str(pth))
'@

$global:LASTEXITCODE = 0
$pthPath = & $VenvPython $pthScript $RepoRoot
if ($LASTEXITCODE -ne 0) {
    Write-Err 'Could not write the .pth file.'
    exit 1
}
Write-Ok "Wrote $pthPath"

# ------------------------------------------------------------- verification ---

Write-Step 'Verifying the install'

$verifyScript = New-TempPythonFile -Name 'verify_install.py' -Content @'
"""Verify a MilSim install. Run from a directory that is NOT the repo root."""
import importlib
import locale
import os
import pathlib
import sys

repo_root = pathlib.Path(sys.argv[1]).resolve()
failures = []

print("  python           : %d.%d.%d" % sys.version_info[:3])
print("  executable       : %s" % sys.executable)
print("  cwd              : %s" % os.getcwd())
print("  PYTHONUTF8       : %s" % os.environ.get("PYTHONUTF8", "<unset>"))
print("  locale encoding  : %s" % locale.getpreferredencoding(False))
print("  filesystem enc.  : %s" % sys.getfilesystemencoding())

MODULES = [
    "openra_env",
    "openra_env.client",
    "openra_env.models",
    "milsim.wego",
    "milsim.commander",
    "milsim.isr",
    "milsim.scenario",
    "milsim.tactical_ai",
    "milsim.cnn",
    "milsim.llm_commander",
    "milsim.game_knowledge",
    "milsim.memory",
    "milsim.spatial",
    "milsim.assessment",
]

print("")
print("  imports:")
for name in MODULES:
    try:
        importlib.import_module(name)
        print("    ok    %s" % name)
    except Exception as exc:  # noqa: BLE001 - report everything
        print("    FAIL  %s -> %s: %s" % (name, type(exc).__name__, exc))
        failures.append(name)

# Optional accelerator.
try:
    from milsim.cnn import _HAS_TORCH
    print("")
    print("  torch available  : %s" % bool(_HAS_TORCH))
except Exception:
    pass

# Exercise the real scenario-loading code path. milsim/scenario.py calls
# open(path) with no encoding=, and the YAML contains U+2014, so this is
# where a non-UTF-8 Windows codepage shows up.
print("")
scenario = repo_root / "milsim" / "mod" / "scenarios" / "hasty_attack.yaml"
try:
    from milsim.scenario import ScenarioRunner
    runner = object.__new__(ScenarioRunner)
    state = runner.load(str(scenario))
    print("  scenario loaded  : %r (%d turns)" % (state.name, state.max_turns))
    if "\u2014" not in state.name:
        print("    FAIL  em dash was mangled - the active codepage is not UTF-8.")
        print("          Set PYTHONUTF8=1 before running milsim.")
        failures.append("scenario-encoding")
    else:
        print("  encoding check   : ok (U+2014 survived the round trip)")
except Exception as exc:  # noqa: BLE001
    print("    FAIL  scenario load -> %s: %s" % (type(exc).__name__, exc))
    failures.append("scenario-load")

print("")
if failures:
    print("  RESULT: FAILED (%d)" % len(failures))
    for name in failures:
        print("    - %s" % name)
    sys.exit(1)

print("  RESULT: all checks passed")
sys.exit(0)
'@

# Deliberately run from a directory that is not the repo root, so that the
# .pth is what makes `import milsim` work - not an accident of the cwd.
$verifyCwd = [System.IO.Path]::GetTempPath()
$verifyExit = 1
Push-Location -LiteralPath $verifyCwd -ErrorAction Stop
try {
    $global:LASTEXITCODE = 0
    & $VenvPython $verifyScript $RepoRoot
    $verifyExit = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ''
if ($verifyExit -ne 0) {
    Write-Err 'Verification failed. See docs\WINDOWS.md (Troubleshooting).'
    exit 1
}

Write-Ok 'Verification passed'

# ---------------------------------------------------------------- summary ---

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor White
Write-Host '  1. Start the OpenRA-RL game server (Docker Desktop must be running):'
Write-Host "       $VenvPython -m openra_env.cli.main server start"
Write-Host '  2. Run the simulation:'
Write-Host '       powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1'
Write-Host '  3. Open the C2 dashboard:'
Write-Host '       powershell -ExecutionPolicy Bypass -File scripts\windows\dashboard.ps1'
Write-Host ''
Write-Host 'Full documentation: docs\WINDOWS.md'
Write-Host ''
exit 0
