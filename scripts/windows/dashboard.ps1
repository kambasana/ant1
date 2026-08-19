<#
.SYNOPSIS
    Open the MilSim C2 dashboard in the default browser.

.DESCRIPTION
    milsim\c2-dashboard.html is a single self-contained page: it makes no
    network calls back to the simulation, so opening it straight off disk
    works. By default this script hands the file:// URI to the shell, which
    routes it to whatever browser is registered as the default.

    Use -Serve if your browser or an enterprise policy restricts file:// pages;
    that starts a local static HTTP server instead and opens http://localhost.

.PARAMETER Browser
    Force a specific browser instead of the system default.

.PARAMETER Serve
    Serve the dashboard over HTTP from a local static server rather than
    opening it from disk. Needs the venv created by setup.ps1.

.PARAMETER Port
    Port for -Serve. Default: 8080.

.PARAMETER PrintOnly
    Print the URI and exit without launching anything.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\dashboard.ps1

.EXAMPLE
    .\scripts\windows\dashboard.ps1 -Serve -Port 8090

.NOTES
    Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#>

[CmdletBinding()]
param(
    [ValidateSet('default', 'chrome', 'msedge', 'firefox')]
    [string] $Browser = 'default',

    [switch] $Serve,
    [int]    $Port = 8080,
    [switch] $PrintOnly,
    [string] $VenvPath = ''
)

$ErrorActionPreference = 'Continue'

function Write-Step { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string] $Message) Write-Host "    OK   $Message" -ForegroundColor Green }
function Write-Err  { param([string] $Message) Write-Host "    FAIL $Message" -ForegroundColor Red }

function Open-Uri {
    param([string] $Uri, [string] $Which)
    if ($Which -eq 'default') {
        # Handing a URI (not a file path) to the shell routes it through the
        # default *browser*, rather than whatever app owns the .html extension.
        Start-Process -FilePath $Uri
    } else {
        Start-Process -FilePath $Which -ArgumentList $Uri
    }
}

# ------------------------------------------------------------------ layout ---

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$MilsimDir = Join-Path $RepoRoot 'milsim'
$DashboardName = 'c2-dashboard.html'
$DashboardPath = Join-Path $MilsimDir $DashboardName

if (-not (Test-Path -LiteralPath $DashboardPath)) {
    Write-Err "Dashboard not found: $DashboardPath"
    Write-Host 'Run this script from inside a full checkout of the repository.' -ForegroundColor Yellow
    exit 1
}
$DashboardPath = (Resolve-Path -LiteralPath $DashboardPath).Path

# ------------------------------------------------------------------- serve ---

if ($Serve) {
    if ($VenvPath -eq '') { $VenvPath = Join-Path $RepoRoot '.venv' }
    $VenvPython = Join-Path (Join-Path $VenvPath 'Scripts') 'python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $posix = Join-Path (Join-Path $VenvPath 'bin') 'python'
        if (Test-Path -LiteralPath $posix) {
            $VenvPython = $posix
        } else {
            Write-Err "No virtual environment at $VenvPath"
            Write-Host 'Run scripts\windows\setup.ps1 first, or drop -Serve.' -ForegroundColor Yellow
            exit 1
        }
    }

    $url = "http://localhost:$Port/$DashboardName"
    Write-Step "Serving $MilsimDir on port $Port"
    Write-Host "    $url"

    if ($PrintOnly) { Write-Host $url; exit 0 }

    # NOTE: -ArgumentList does not quote array elements on Windows PowerShell
    # 5.1, so passing the directory as an argument breaks on paths containing
    # spaces (C:\Users\John Doe\...). -WorkingDirectory is passed out of band
    # and http.server serves the current directory by default.
    $serverArgs = @('-m', 'http.server', "$Port")
    $proc = Start-Process -FilePath $VenvPython -ArgumentList $serverArgs -WorkingDirectory $MilsimDir -PassThru -NoNewWindow
    if ($null -eq $proc) {
        Write-Err 'Could not start the static file server.'
        exit 1
    }

    try {
        Start-Sleep -Seconds 1
        if ($proc.HasExited) {
            Write-Err "The static server exited immediately (code $($proc.ExitCode)). Port $Port is probably already in use."
            Write-Host "Try a different port: .\scripts\windows\dashboard.ps1 -Serve -Port 8091" -ForegroundColor Yellow
            exit 1
        }
        Open-Uri -Uri $url -Which $Browser
        Write-Ok 'Dashboard opened.'
        Write-Host ''
        Write-Host 'Press Ctrl+C to stop the server.' -ForegroundColor Yellow
        $proc.WaitForExit()
    } finally {
        if ($null -ne $proc -and -not $proc.HasExited) {
            $proc.Kill()
            Write-Host ''
            Write-Host 'Static server stopped.'
        }
    }
    exit 0
}

# ---------------------------------------------------------------- file:// ---

# Use Uri::TryCreate rather than a [System.Uri] cast: the cast yields a
# *relative* Uri (and therefore an empty AbsoluteUri) for POSIX-style paths,
# and returns nothing useful if the path is ever unusual. TryCreate escapes
# spaces correctly, which matters for C:\Users\First Last\...
$uriObject = $null
if (-not [System.Uri]::TryCreate($DashboardPath, [System.UriKind]::Absolute, [ref] $uriObject)) {
    Write-Err "Could not build a file:// URI for $DashboardPath"
    exit 1
}
$uri = $uriObject.AbsoluteUri
if ([string]::IsNullOrWhiteSpace($uri)) {
    Write-Err "Built an empty URI for $DashboardPath"
    exit 1
}

Write-Step 'Opening the MilSim C2 dashboard'
Write-Host "    $uri"

if ($PrintOnly) { Write-Host $uri; exit 0 }

try {
    Open-Uri -Uri $uri -Which $Browser
} catch {
    Write-Err "Could not open a browser: $($_.Exception.Message)"
    Write-Host ''
    Write-Host 'Open this path by hand instead:' -ForegroundColor Yellow
    Write-Host "  $DashboardPath" -ForegroundColor Yellow
    Write-Host 'Or serve it over HTTP:' -ForegroundColor Yellow
    Write-Host '  .\scripts\windows\dashboard.ps1 -Serve' -ForegroundColor Yellow
    exit 1
}

Write-Ok 'Dashboard opened.'
Write-Host ''
Write-Host 'The page pulls Inter and IBM Plex Mono from Google Fonts on first load.' -ForegroundColor DarkGray
Write-Host 'Offline it falls back to Consolas / system UI fonts; nothing else breaks.' -ForegroundColor DarkGray
exit 0
