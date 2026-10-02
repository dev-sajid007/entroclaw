# entroclaw installer for Windows (PowerShell 5.1+).
#
#   irm https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.ps1 | iex
#
# To pass options, download first:  & ([scriptblock]::Create((irm <url>))) -Version 0.2.0
#   -Version X.Y.Z     install a specific release (default: latest; or $env:ENTROCLAW_VERSION)
#   -NoModifyPath      don't add the install directory to your user PATH
#   -Uninstall         remove entroclaw (keeps your config and sessions)
#
# Environment: ENTROCLAW_INSTALL_DIR (default %USERPROFILE%\.entroclaw\bin), ENTROCLAW_RELEASE_BASE.
param(
    [string]$Version = $env:ENTROCLAW_VERSION,
    [switch]$NoModifyPath,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Repo = "dev-sajid007/entroclaw"
$InstallDir = if ($env:ENTROCLAW_INSTALL_DIR) { $env:ENTROCLAW_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".entroclaw\bin" }
$Base = if ($env:ENTROCLAW_RELEASE_BASE) { $env:ENTROCLAW_RELEASE_BASE } else { "https://github.com/$Repo/releases" }

function Ok($message) { Write-Host "✓ $message" -ForegroundColor Green }
function Fail($message) { Write-Host "error: $message" -ForegroundColor Red; exit 1 }

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    Write-Host "Installing uv (Python package manager, https://docs.astral.sh/uv/)..."
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
    $uv = "$env:USERPROFILE\.local\bin\uv.exe"
    if (-not (Test-Path $uv)) { Fail "uv installation failed; install it manually and re-run" }
    return $uv
}

if ($Uninstall) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $InstallDir "entroclaw.exe")
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) { & $uv.Source tool uninstall entroclaw-agent 2>$null | Out-Null }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $cleaned = ($userPath -split ";" | Where-Object { $_ -and $_ -ne $InstallDir }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $cleaned, "User")
    }
    Ok "entroclaw removed. Settings remain in $env:APPDATA\entroclaw and sessions in $env:LOCALAPPDATA\entroclaw."
    exit 0
}

$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { "x64" }
    "ARM64" { "x64" }  # x64 build runs under emulation on Windows on Arm
    default { Fail "unsupported architecture: $env:PROCESSOR_ARCHITECTURE" }
}
$target = "windows-$arch"

$Version = "$Version".TrimStart("v")
if (-not $Version) {
    if ($Base -like "https://github.com/*") {
        $Version = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name.TrimStart("v")
    }
    if (-not $Version) { Fail "could not determine the latest version; pass -Version" }
}
$AssetBase = if ($Base -like "https://github.com/*") { "$Base/download/v$Version" } else { "$Base/v$Version" }
$archive = "entroclaw-$target.zip"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("entroclaw-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null

function Get-Asset($name) {
    $dest = Join-Path $tmp $name
    if ($AssetBase -like "file://*") { Copy-Item ($AssetBase.Substring(7) + "/" + $name) $dest }
    else { Invoke-WebRequest -UseBasicParsing -Uri "$AssetBase/$name" -OutFile $dest }
    return $dest
}

try {
    Write-Host "Installing entroclaw $Version ($target)..."
    $zip = Get-Asset $archive
    $sums = Get-Asset "SHA256SUMS"
    $line = Get-Content $sums | Where-Object { $_ -match " $([regex]::Escape($archive))$" } | Select-Object -First 1
    if (-not $line) { Fail "$archive is not listed in SHA256SUMS" }
    $expected = ($line -split " ")[0].ToLower()
    $actual = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($actual -ne $expected) { Fail "checksum mismatch for $archive; refusing to install" }
    Ok "checksum verified"

    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Force (Join-Path $tmp "entroclaw.exe") (Join-Path $InstallDir "entroclaw.exe")
    Ok "installed $InstallDir\entroclaw.exe"

    $uv = Find-Uv
    $wheel = "entroclaw_agent-$Version-py3-none-any.whl"
    $spec = if ($AssetBase -like "file://*") { $AssetBase.Substring(7) + "/" + $wheel } else { "$AssetBase/$wheel" }
    & $uv tool install --force --quiet --python 3.12 $spec
    if ($LASTEXITCODE -ne 0) { Fail "installing the agent failed" }
    Ok "installed the agent (entroclaw-agent) with uv"
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
}

if (-not $NoModifyPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (($userPath -split ";") -contains $InstallDir)) {
        [Environment]::SetEnvironmentVariable("Path", (($userPath, $InstallDir) | Where-Object { $_ }) -join ";", "User")
        $env:Path = "$env:Path;$InstallDir"
        Ok "added $InstallDir to your user PATH (open a new terminal)"
    }
}

Write-Host ""
Write-Host "Done. Next:"
Write-Host "  entroclaw auth              # add an API key"
Write-Host "  cd your-project; entroclaw"
Write-Host "Tip: install Git for Windows so the agent can run commands with bash."
