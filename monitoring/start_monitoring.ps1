# BuildSense -- Automated Windows Standalone Prometheus and Grafana Launcher
# Starts Prometheus and Grafana native Windows executables in background processes.

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$binDir = Join-Path $scriptDir "bin"
$promBinDir = Join-Path $binDir "prometheus"
$grafBinDir = Join-Path $binDir "grafana"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " BuildSense -- Launching Windows Prometheus and Grafana Stack     " -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Verify Binaries
# ---------------------------------------------------------------------------
$promExe = Join-Path $promBinDir "prometheus.exe"
if (-not (Test-Path $promExe)) {
    Write-Host "ERROR: Prometheus binary not found at $promExe" -ForegroundColor Red
    Write-Host "Please run .\monitoring\setup_windows_monitoring.ps1 first." -ForegroundColor Yellow
    exit 1
}

$grafExe = Join-Path $grafBinDir "bin\grafana-server.exe"
if (-not (Test-Path $grafExe)) {
    $grafExe = Join-Path $grafBinDir "bin\grafana.exe"
}
if (-not (Test-Path $grafExe)) {
    Write-Host "ERROR: Grafana executable not found in $grafBinDir" -ForegroundColor Red
    Write-Host "Please run .\monitoring\setup_windows_monitoring.ps1 first." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Launch Prometheus
# ---------------------------------------------------------------------------
Write-Host "[1/2] Starting Prometheus Server..." -ForegroundColor Yellow
$promConfig = Join-Path $promBinDir "prometheus.yml"
$promProcess = Start-Process -FilePath $promExe -ArgumentList "--config.file=""$promConfig""" -WorkingDirectory $promBinDir -PassThru -WindowStyle Hidden

Write-Host "      Prometheus running (PID: $($promProcess.Id))" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. Launch Grafana
# ---------------------------------------------------------------------------
Write-Host "[2/2] Starting Grafana Server..." -ForegroundColor Yellow
$grafProcess = Start-Process -FilePath $grafExe -ArgumentList "server" -WorkingDirectory $grafBinDir -PassThru -WindowStyle Hidden

Write-Host "      Grafana running (PID: $($grafProcess.Id))" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. Print Status and Direct Access URLs
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host " BuildSense Monitoring Stack is RUNNING (No Docker Required)      " -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "  Flask Metrics Stream : http://localhost:5000/metrics" -ForegroundColor Cyan
Write-Host "  Prometheus Server UI : http://localhost:9090" -ForegroundColor Cyan
Write-Host "  Grafana Dashboard UI : http://localhost:3000 (User: admin / Pass: admin)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Green
