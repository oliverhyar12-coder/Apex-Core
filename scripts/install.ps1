# APEX CORE installer for Windows (PowerShell 5.1 or newer). No pipx needed: it
# creates a private virtual environment and puts `apex` on your user PATH.
#
#   From a clone:   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   From GitHub:    irm https://raw.githubusercontent.com/oliverhyar12-coder/Apex-Core/main/scripts/install.ps1 | iex
#
# Options (as parameters when run as a file, or environment variables):
#   -Ref BRANCH / $env:APEX_REF        install that branch from GitHub (default: main)
#   -Source PATH / $env:APEX_SOURCE    install from a local checkout or archive
#   -WithKeyring                       also install Windows Credential Manager support
#   -Uninstall                         remove the app (your config and history are kept)
#   $env:APEX_INSTALL_DIR              where to put it (default %LOCALAPPDATA%\apex-core)
param(
    [string]$Ref = $env:APEX_REF,
    [string]$Source = $env:APEX_SOURCE,
    [switch]$WithKeyring,
    [switch]$Uninstall
)

# Everything below runs in a child scope, so `irm ... | iex` (which runs in your own
# session) does not leave StrictMode, ErrorActionPreference, or its working variables
# behind.
& {
param([string]$ScriptDir)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3

$RepoUrl = if ($env:APEX_REPO_URL) { $env:APEX_REPO_URL } else { "https://github.com/oliverhyar12-coder/Apex-Core" }
if (-not $Ref) { $Ref = "main" }
$Root = if ($env:APEX_INSTALL_DIR) { $env:APEX_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "apex-core" }
$VenvDir = Join-Path $Root "venv"
$BinDir = Join-Path $Root "bin"
$Extras = if ($WithKeyring) { "[keyring]" } else { "" }

function Info($Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
# throw (not exit): with `irm ... | iex`, exit would close the user's PowerShell window.
function Fail($Message) { throw "APEX CORE install failed: $Message" }

function Invoke-Native {
    # Run a native command and stop if it fails (PowerShell 5.1 does not do this itself).
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "$FilePath $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
}

function Get-UserPath { [Environment]::GetEnvironmentVariable("Path", "User") }

if ($Uninstall) {
    if (Test-Path (Join-Path $VenvDir "pyvenv.cfg")) { Remove-Item -Recurse -Force $VenvDir; Info "Removed $VenvDir" }
    if (Test-Path $BinDir) { Remove-Item -Recurse -Force $BinDir; Info "Removed $BinDir" }
    $userPath = Get-UserPath
    if ($userPath) {
        $kept = ($userPath -split ";") | Where-Object { $_ -and ($_ -ne $BinDir) }
        [Environment]::SetEnvironmentVariable("Path", ($kept -join ";"), "User")
    }
    Write-Host "Your settings and history were kept (see 'apex config path' before uninstalling)."
    return
}

# --- Find Python 3.11+ -----------------------------------------------------------------
$check = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
$python = $null
$candidates = @(
    @("py", "-3.14"), @("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3"),
    @("python"), @("python3")
)
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $extra = @($candidate | Select-Object -Skip 1)
    try {
        & $exe @extra -c $check 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    } catch { continue }
}
if (-not $python) {
    Fail "Python 3.11 or newer is required. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH') and run this again."
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)
$version = & $pyExe @pyArgs -c "import sys; print(sys.version.split()[0])"
Info "Using Python $version ($($python -join ' '))"

# --- What to install ---------------------------------------------------------------------
if (-not $Source -and $ScriptDir) {
    $pyproject = Join-Path (Split-Path $ScriptDir -Parent) "pyproject.toml"
    if ((Test-Path $pyproject) -and (Select-String -Path $pyproject -Pattern 'name = "apex-core-cli"' -Quiet)) {
        $Source = Split-Path $ScriptDir -Parent
    }
}
if ($Source) {
    if ($Source -match "^https?://") { $spec = "apex-core-cli$Extras @ $Source" }
    else {
        if (-not (Test-Path $Source)) { Fail "-Source $Source does not exist" }
        $spec = (Resolve-Path $Source).Path + $Extras
    }
    Info "Installing from $Source"
} else {
    $spec = "apex-core-cli$Extras @ $RepoUrl/archive/refs/heads/$Ref.tar.gz"
    Info "Installing branch '$Ref' from $RepoUrl"
}

# --- Environment and install --------------------------------------------------------------
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Info "Creating a private environment in $VenvDir"
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    Invoke-Native $pyExe ($pyArgs + @("-m", "venv", $VenvDir))
}
$pipArgs = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check")
Invoke-Native $venvPython ($pipArgs + @("--upgrade", "pip"))
Info "Installing APEX CORE and its dependencies (this can take a minute)"
Invoke-Native $venvPython ($pipArgs + @("--upgrade", $spec))
Invoke-Native $venvPython ($pipArgs + @("--force-reinstall", "--no-deps", $spec))

# Copy only the apex launcher into its own folder, so the environment's python.exe
# does not shadow yours on PATH. (A .cmd shim would turn Ctrl+C into a
# "Terminate batch job?" prompt, so the real launcher is copied instead.)
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Force (Join-Path $VenvDir "Scripts\apex.exe") (Join-Path $BinDir "apex.exe")
$apex = Join-Path $BinDir "apex.exe"
$installed = & $apex --version
if ($LASTEXITCODE -ne 0) { Fail "the installed apex command does not run" }

$userPath = Get-UserPath
$entries = if ($userPath) { $userPath -split ";" } else { @() }
$pathChanged = $false
if ($entries -notcontains $BinDir) {
    $newPath = if ($userPath) { "$BinDir;$userPath" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$BinDir;$env:Path"
    $pathChanged = $true
    Info "Added $BinDir to your user PATH"
}

Write-Host ""
Write-Host "Installed $installed." -ForegroundColor Green
if ($pathChanged) {
    Write-Host "Open a new terminal window, then run:  apex"
} else {
    Write-Host "Run:  apex   (the first run walks you through setup)"
}
Write-Host "Update later by running this installer again; remove it with -Uninstall."
} $PSScriptRoot
