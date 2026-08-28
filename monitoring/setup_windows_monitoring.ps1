# BuildSense -- Automated Windows Standalone Prometheus and Grafana Installer
# Downloads standalone zip packages for Prometheus and Grafana, extracts them into local subdirectories,
# and prepares the monitoring configuration without requiring Docker or virtualization.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$binDir = Join-Path $scriptDir "bin"
$promBinDir = Join-Path $binDir "prometheus"
$grafBinDir = Join-Path $binDir "grafana"
$tmpDir = Join-Path $scriptDir "tmp"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " BuildSense -- Setting up Windows Standalone Prometheus and Grafana " -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# Create working directories
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
New-Item -ItemType Directory -Force -Path $promBinDir | Out-Null
New-Item -ItemType Directory -Force -Path $grafBinDir | Out-Null
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# ---------------------------------------------------------------------------
# 1. Download and Install Prometheus (Windows AMD64)
# ---------------------------------------------------------------------------
$promZipPath = Join-Path $tmpDir "prometheus.zip"
$promUrl = "https://github.com/prometheus/prometheus/releases/download/v2.50.1/prometheus-2.50.1.windows-amd64.zip"

if (-not (Test-Path (Join-Path $promBinDir "prometheus.exe"))) {
    if (Test-Path $promZipPath) {
        Remove-Item -Path $promZipPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[1/2] Downloading Prometheus standalone package..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $promUrl -OutFile $promZipPath -UseBasicParsing
    
    Write-Host "      Extracting Prometheus to $promBinDir..." -ForegroundColor Yellow
    Expand-Archive -Path $promZipPath -DestinationPath $tmpDir -Force
    
    $extractedPromFolder = Get-ChildItem -Path $tmpDir -Filter "prometheus-*" -Directory | Select-Object -First 1
    if ($extractedPromFolder) {
        Copy-Item -Path "$($extractedPromFolder.FullName)\*" -Destination $promBinDir -Recurse -Force
    }
} else {
    Write-Host "[1/2] Prometheus is already installed in $promBinDir" -ForegroundColor Green
}

# Copy monitoring/prometheus.yml to Prometheus binary directory
$promConfigSrc = Join-Path $scriptDir "prometheus.yml"
$promConfigDst = Join-Path $promBinDir "prometheus.yml"
if (Test-Path $promConfigSrc) {
    Copy-Item -Path $promConfigSrc -Destination $promConfigDst -Force
    Write-Host "      Copied prometheus.yml config to $promConfigDst" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 2. Download and Install Grafana (Windows AMD64)
# ---------------------------------------------------------------------------
$grafZipPath = Join-Path $tmpDir "grafana.zip"
$grafUrl = "https://dl.grafana.com/oss/release/grafana-10.3.3.windows-amd64.zip"

if (-not (Test-Path (Join-Path $grafBinDir "bin\grafana-server.exe")) -and -not (Test-Path (Join-Path $grafBinDir "bin\grafana.exe"))) {
    if (Test-Path $grafZipPath) {
        Remove-Item -Path $grafZipPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[2/2] Downloading Grafana standalone package..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $grafUrl -OutFile $grafZipPath -UseBasicParsing
    
    Write-Host "      Extracting Grafana to $grafBinDir..." -ForegroundColor Yellow
    Expand-Archive -Path $grafZipPath -DestinationPath $tmpDir -Force
    
    $extractedGrafFolder = Get-ChildItem -Path $tmpDir -Filter "grafana-*" -Directory | Select-Object -First 1
    if ($extractedGrafFolder) {
        Copy-Item -Path "$($extractedGrafFolder.FullName)\*" -Destination $grafBinDir -Recurse -Force
    }
} else {
    Write-Host "[2/2] Grafana is already installed in $grafBinDir" -ForegroundColor Green
}

# Clean up temp directory
Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host " Setup Complete! Prometheus and Grafana are ready to run." -ForegroundColor Green
Write-Host " Run .\monitoring\start_monitoring.ps1 to launch the servers." -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
