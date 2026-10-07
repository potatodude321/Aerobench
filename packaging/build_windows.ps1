<#
  Build the Windows installer (run on Windows 10/11 x64 in PowerShell from the repo root).

    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 [-NoCad] [-PythonSha256 <hash>]

  Requires: Python 3.11+ on PATH (build tool only), Node.js 20+, Git, 7-Zip (7z.exe).
  Output:   desktop\release\AeroBench-Setup-<version>.exe
            packaging\build\sources\  (GPL source archives: publish next to every installer)

  Steps
    1. engine unit tests (fail fast)
    2. bundled applications: OpenFOAM v2606 (Windows), ParaView 6.2.0, MS-MPI 10.1.3
                                                  -> packaging\build\bundle (pinned SHA-256)
    3. embedded CPython + engine + dependencies  -> packaging\build\python
    4. desktop renderer + Electron main/preload   -> desktop\dist, desktop\dist-electron
    5. electron-builder NSIS installer (per-machine, one administrator prompt; installs MS-MPI
       silently when missing)
#>
param(
  [switch]$NoCad,
  [string]$PythonVersion = "3.12.10",
  [string]$PythonSha256 = "",
  [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

if (-not $SkipTests) {
  Step "Engine unit tests"
  python -m venv .build-venv
  .\.build-venv\Scripts\python -m pip install -q --upgrade pip
  .\.build-venv\Scripts\python -m pip install -q -e "engine[dev]"
  .\.build-venv\Scripts\python -m pytest engine\tests\unit -q
  if ($LASTEXITCODE -ne 0) { throw "unit tests failed" }
}

Step "Bundled applications (OpenFOAM, ParaView, MS-MPI)"
python packaging\prepare_thirdparty.py
if ($LASTEXITCODE -ne 0) { throw "prepare_thirdparty failed" }

Step "Embedded Python runtime"
$args = @("packaging\prepare_python.py", "--python-version", $PythonVersion)
if ($NoCad) { $args += "--no-cad" }
if ($PythonSha256) { $args += @("--sha256", $PythonSha256) }
python @args
if ($LASTEXITCODE -ne 0) { throw "prepare_python failed" }

Step "Smoke test of the embedded engine"
& packaging\build\python\python.exe -c "import aerobench, aerobench.api.app, numpy, scipy; from aerobench.geometry.cad import available_backends; print('engine ok; CAD back-ends:', available_backends())"
if ($LASTEXITCODE -ne 0) { throw "embedded engine does not import" }
& packaging\build\python\python.exe -m aerobench.cli doctor
# exit code 2 only means OpenFOAM is not installed on the build machine

Step "Desktop build"
Push-Location desktop
npm ci
npm run build
if ($LASTEXITCODE -ne 0) { throw "desktop build failed" }

Step "Installer"
npx electron-builder --win nsis --x64 --publish never
if ($LASTEXITCODE -ne 0) { throw "electron-builder failed" }
Pop-Location

Get-ChildItem desktop\release\*.exe | ForEach-Object {
  $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
  Write-Host "Built $($_.Name)  SHA256 $h" -ForegroundColor Green
}
