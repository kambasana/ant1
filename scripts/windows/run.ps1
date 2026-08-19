<#
.SYNOPSIS
    Launch the MilSim simulation on Windows.

.DESCRIPTION
    Wraps `python -m milsim`. Sets UTF-8 mode, resolves the scenario to an
    absolute path, checks that the OpenRA-RL game server is actually
    reachable before starting, and reports a useful diagnosis when it is not.

    Run scripts\windows\setup.ps1 first.

.PARAMETER Mode
    Commander mode passed through to milsim:
      rule - scripted tactical AI, no LLM required (default)
      text - structured-text LLM commander (needs -Model)
      fc   - function-calling LLM commander (needs -Model)
      mcp  - run as an MCP stdio server for Claude Desktop / Claude Code

.PARAMETER Server
    OpenRA-RL server URL. Default: http://localhost:8000

.PARAMETER Scenario
    Scenario YAML. Accepts a bare name ("hasty_attack"), a repo-relative path
    or an absolute path. Default: hasty_attack.

.PARAMETER Ollama
    Point the LLM modes at a local Ollama instance and check that it is up.

.PARAMETER OllamaHost
    Base address of the Ollama daemon. Default: http://localhost:11434.
    Implies -Ollama, and is exported as OLLAMA_HOST for the milsim.llm_provider
    code path in addition to being passed as --base-url.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1

.EXAMPLE
    .\scripts\windows\run.ps1 -Mode text -Ollama -Model qwen2.5:7b-instruct

.EXAMPLE
    .\scripts\windows\run.ps1 -Scenario strait_of_hormuz -MaxTurns 20 -Fast

.NOTES
    Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#>

[CmdletBinding()]
param(
    [ValidateSet('rule', 'text', 'fc', 'mcp')]
    [string] $Mode = 'rule',

    [string] $Server = 'http://localhost:8000',
    [string] $Scenario = 'hasty_attack',
    [int]    $MaxTurns = 60,

    [string] $Model = '',
    [string] $BaseUrl = '',
    [string] $ApiKey = '',
    [switch] $Ollama,
    [string] $OllamaHost = '',

    [switch] $Fast,
    [switch] $Quiet,
    [switch] $SkipHealthCheck,
    [string] $VenvPath = ''
)

# See the note in setup.ps1: 'Stop' plus native stderr is a 5.1 trap.
$ErrorActionPreference = 'Continue'

function Write-Step { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string] $Message) Write-Host "    OK   $Message" -ForegroundColor Green }
function Write-Warn { param([string] $Message) Write-Host "    WARN $Message" -ForegroundColor Yellow }
function Write-Err  { param([string] $Message) Write-Host "    FAIL $Message" -ForegroundColor Red }

function Test-TcpPort {
    param([string] $ComputerName, [int] $Port, [int] $TimeoutMs = 2000)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $handle = $client.BeginConnect($ComputerName, $Port, $null, $null)
        if (-not $handle.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($handle)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-HttpEndpoint {
    param([string] $Uri, [int] $TimeoutSec = 5)
    try {
        # -UseBasicParsing matters on 5.1, where the default engine needs IE.
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400)
    } catch {
        return $false
    }
}

# ------------------------------------------------------------------ layout ---

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ($VenvPath -eq '') { $VenvPath = Join-Path $RepoRoot '.venv' }

$VenvPython = Join-Path (Join-Path $VenvPath 'Scripts') 'python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    $posix = Join-Path (Join-Path $VenvPath 'bin') 'python'
    if (Test-Path -LiteralPath $posix) {
        $VenvPython = $posix
    } else {
        Write-Err "No virtual environment at $VenvPath"
        Write-Host ''
        Write-Host 'Run setup first:' -ForegroundColor Yellow
        Write-Host '  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1' -ForegroundColor Yellow
        exit 1
    }
}

# milsim\scenario.py opens YAML without an explicit encoding and the scenario
# files contain U+2014. Without UTF-8 mode this mangles text on cp1252 systems
# and raises UnicodeDecodeError on cp932/cp949 systems.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# ---------------------------------------------------------------- scenario ---

$ScenarioDir = Join-Path (Join-Path $RepoRoot 'milsim') (Join-Path 'mod' 'scenarios')

if ([System.IO.Path]::IsPathRooted($Scenario)) {
    $ScenarioPath = $Scenario
} elseif (Test-Path -LiteralPath (Join-Path $RepoRoot $Scenario)) {
    $ScenarioPath = (Resolve-Path -LiteralPath (Join-Path $RepoRoot $Scenario)).Path
} else {
    $name = $Scenario
    if (-not $name.EndsWith('.yaml')) { $name = "$name.yaml" }
    $ScenarioPath = Join-Path $ScenarioDir $name
}

if (-not (Test-Path -LiteralPath $ScenarioPath)) {
    Write-Err "Scenario not found: $ScenarioPath"
    Write-Host ''
    Write-Host 'Available scenarios:' -ForegroundColor Yellow
    Get-ChildItem -LiteralPath $ScenarioDir -Filter '*.yaml' | ForEach-Object {
        Write-Host "  $($_.BaseName)" -ForegroundColor Yellow
    }
    exit 1
}
$ScenarioPath = (Resolve-Path -LiteralPath $ScenarioPath).Path

# --------------------------------------------------------------------- LLM ---

if ($Ollama -or $OllamaHost -ne '') {
    if ($Mode -eq 'rule') {
        Write-Warn '-Ollama has no effect in rule mode; switching to -Mode text.'
        $Mode = 'text'
    }
    if ($OllamaHost -eq '') { $OllamaHost = 'http://localhost:11434' }
    $OllamaHost = $OllamaHost.TrimEnd('/')

    # milsim's text/fc modes take --base-url. milsim\llm_provider.py (used by
    # the tactical AI) reads OLLAMA_HOST instead, so set both.
    $env:OLLAMA_HOST = $OllamaHost
    if ($BaseUrl -eq '') { $BaseUrl = "$OllamaHost/v1" }
    if ($ApiKey -eq '')  { $ApiKey = 'ollama' }

    Write-Step "Checking Ollama at $OllamaHost"
    if (Test-HttpEndpoint -Uri "$OllamaHost/api/tags") {
        Write-Ok 'Ollama is responding'
    } else {
        Write-Err "Ollama is not responding at $OllamaHost"
        Write-Host ''
        Write-Host 'Install it from https://ollama.com/download/windows, then:' -ForegroundColor Yellow
        Write-Host '  ollama serve          # usually already running as a service' -ForegroundColor Yellow
        Write-Host '  ollama pull qwen2.5:7b-instruct' -ForegroundColor Yellow
        Write-Host '  ollama list           # names you can pass to -Model' -ForegroundColor Yellow
        Write-Host ''
        Write-Host 'If Ollama runs on another machine, pass its address:' -ForegroundColor Yellow
        Write-Host '  .\scripts\windows\run.ps1 -Mode text -OllamaHost http://192.168.1.20:11434 -Model qwen2.5:7b-instruct' -ForegroundColor Yellow
        exit 1
    }
}

if (($Mode -eq 'text' -or $Mode -eq 'fc') -and $Model -eq '') {
    Write-Err "-Model is required for -Mode $Mode."
    Write-Host ''
    Write-Host 'Examples:' -ForegroundColor Yellow
    Write-Host '  .\scripts\windows\run.ps1 -Mode text -Ollama -Model qwen2.5:7b-instruct' -ForegroundColor Yellow
    Write-Host '  .\scripts\windows\run.ps1 -Mode fc -Model gpt-4o -ApiKey $env:OPENAI_API_KEY' -ForegroundColor Yellow
    exit 1
}

# ------------------------------------------------------------ game server ---

if (-not $SkipHealthCheck -and $Mode -ne 'mcp') {
    Write-Step "Checking the OpenRA-RL server at $Server"

    $uri = $null
    try { $uri = [System.Uri] $Server } catch { $uri = $null }

    $healthy = Test-HttpEndpoint -Uri "$($Server.TrimEnd('/'))/health"

    if ($healthy) {
        Write-Ok 'Server is healthy'
    } else {
        $portOpen = $false
        if ($null -ne $uri) {
            $port = $uri.Port
            if ($port -le 0) { $port = 8000 }
            $portOpen = Test-TcpPort -ComputerName $uri.Host -Port $port
        }

        if ($portOpen) {
            Write-Err "Something is listening on $Server but /health did not answer."
            Write-Host ''
            Write-Host 'Check the server logs:' -ForegroundColor Yellow
            Write-Host "  $VenvPython -m openra_env.cli.main server logs" -ForegroundColor Yellow
            Write-Host 'A corporate HTTP proxy can also intercept localhost. If so, add' -ForegroundColor Yellow
            Write-Host 'localhost to the proxy bypass list, or re-run with -SkipHealthCheck.' -ForegroundColor Yellow
        } else {
            Write-Err "Nothing is listening at $Server."
            Write-Host ''
            Write-Host 'Start the game server first (Docker Desktop must be running):' -ForegroundColor Yellow
            Write-Host "  $VenvPython -m openra_env.cli.main server start" -ForegroundColor Yellow
            Write-Host ''
            Write-Host 'Check prerequisites with:' -ForegroundColor Yellow
            Write-Host "  $VenvPython -m openra_env.cli.main doctor" -ForegroundColor Yellow
            Write-Host ''
            Write-Host 'See docs\WINDOWS.md for the Docker Desktop / WSL2 setup.' -ForegroundColor Yellow
        }
        exit 1
    }
}

# ------------------------------------------------------------------- launch ---

$argList = @(
    '-m', 'milsim',
    '--mode', $Mode,
    '--server', $Server,
    '--scenario', $ScenarioPath,
    '--max-turns', "$MaxTurns"
)
if ($Fast)  { $argList += '--fast' }
if ($Quiet) { $argList += '--quiet' }
if ($Model -ne '')   { $argList += @('--model', $Model) }
if ($BaseUrl -ne '') { $argList += @('--base-url', $BaseUrl) }
if ($ApiKey -ne '')  { $argList += @('--api-key', $ApiKey) }

Write-Step "Starting MilSim (mode: $Mode)"
Write-Host "    scenario   $ScenarioPath"
Write-Host "    server     $Server"
Write-Host "    python     $VenvPython"
Write-Host ''

# `python -m milsim` needs the repo root importable. setup.ps1 installs a .pth
# for that, but running from the repo root keeps it working even if the .pth
# was removed or the venv was rebuilt by hand.
$exitCode = 1
Push-Location -LiteralPath $RepoRoot -ErrorAction Stop
try {
    $global:LASTEXITCODE = 0
    & $VenvPython @argList
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ''
if ($exitCode -ne 0) {
    Write-Err "MilSim exited with code $exitCode."
    Write-Host 'See docs\WINDOWS.md (Troubleshooting).' -ForegroundColor Yellow
} else {
    Write-Ok 'MilSim exited cleanly.'
}
exit $exitCode
