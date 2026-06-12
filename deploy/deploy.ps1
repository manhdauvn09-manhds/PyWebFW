<#
.SYNOPSIS
    Deploy PyWebFW containers - locally or to a remote server over SSH.

.DESCRIPTION
    One script for every deployment shape:
      -Target fe         : public front-end container only   (port 8001)
      -Target admin      : admin container only              (port 8002)
      -Target scheduler  : scheduler/cron container only     (no public port)
      -Target all        : fe + admin + scheduler (split mode)
      -Target allinone   : single container with every module (port 8000)

    Without -Server the deploy runs on the local Docker engine.
    With -Server the project is packaged, copied over SSH (OpenSSH client),
    and docker compose runs on the remote host.

    With -Domain the Caddy reverse proxy is deployed too: automatic HTTPS
    (Let's Encrypt + renewal), HTTP->HTTPS redirect and HSTS. Point your
    domains' DNS A records at the server first.

.EXAMPLE
    .\deploy.ps1 -Target all
.EXAMPLE
    .\deploy.ps1 -Target fe -Server 10.0.0.21 -User deploy
.EXAMPLE
    .\deploy.ps1 -Target all -Server web01 -User deploy -Domain example.com
.EXAMPLE
    .\deploy.ps1 -Target allinone -Server web01.example.com -User ubuntu -Domain example.com -AdminDomain admin.example.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("fe", "admin", "scheduler", "all", "allinone")]
    [string]$Target,

    [string]$Server,                       # empty = deploy on local Docker
    [string]$User = "deploy",
    [int]$SshPort = 22,
    [string]$RemotePath = "/opt/pywebfw",
    [string]$EnvFile = ".env",
    [string]$Domain,                       # e.g. example.com -> enables Caddy HTTPS
    [string]$AdminDomain,                  # default: admin.<Domain>
    [switch]$NoBuild,                      # reuse existing image
    [switch]$SkipHealthCheck
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

# --- helpers -----------------------------------------------------------------
function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host " OK $Message" -ForegroundColor Green }
function Fail([string]$Message)       { Write-Host " !! $Message" -ForegroundColor Red; exit 1 }

function New-RandomSecret {
    $chars = ([char[]](48..57)) + ([char[]](65..90)) + ([char[]](97..122))
    -join (1..56 | ForEach-Object { $chars | Get-Random })
}

function Resolve-ComposeArgs([string]$DeployTarget, [bool]$Build, [bool]$WithProxy) {
    $buildFlag = @()
    if ($Build) { $buildFlag = @("--build") }
    $profiles = @()
    $proxySvc = @()
    if ($WithProxy) { $profiles += @("--profile", "proxy"); $proxySvc = @("caddy") }
    switch ($DeployTarget) {
        "all"      { return @("compose") + $profiles + @("up", "-d") + $buildFlag + @("fe", "admin", "scheduler") + $proxySvc }
        "allinone" { return @("compose", "--profile", "allinone") + $profiles + @("up", "-d") + $buildFlag + @("allinone") + $proxySvc }
        default    {
            # Dedicated-server deploy: do NOT drag dependent services along.
            return @("compose") + $profiles + @("up", "-d", "--no-deps") + $buildFlag + @($DeployTarget) + $proxySvc
        }
    }
}

function Set-EnvVar([string]$Path, [string]$Key, [string]$Value) {
    # Idempotent: replaces the line if the key exists, appends otherwise.
    $lines = @()
    if (Test-Path $Path) { $lines = @(Get-Content $Path) }
    $found = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match "^$Key=") { $found = $true; "$Key=$Value" } else { $_ }
    }
    if (-not $found) { $lines += "$Key=$Value" }
    $lines | Out-File -FilePath $Path -Encoding ascii
}

function Get-HealthPort([string]$DeployTarget) {
    switch ($DeployTarget) {
        "fe"       { return 8001 }
        "admin"    { return 8002 }
        "allinone" { return 8000 }
        "all"      { return 8001 }
        default    { return $null }       # scheduler: no host port
    }
}

function Test-LocalHealth([string]$DeployTarget) {
    $port = Get-HealthPort $DeployTarget
    if ($null -eq $port) {
        Write-Step "scheduler has no host port - checking container health state"
        $state = docker inspect --format "{{.State.Health.Status}}" pywebfw-scheduler-1 2>$null
        if ($state -eq "healthy") { Write-Ok "scheduler container is healthy"; return }
        Write-Host " .. container state: $state (may still be starting)" -ForegroundColor Yellow
        return
    }
    Write-Step "waiting for http://localhost:$port/healthz"
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:$port/healthz" -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                Write-Ok "healthz responded: $($response.Content)"
                return
            }
        } catch { Start-Sleep -Seconds 3 }
    }
    Fail "health check timed out on port $port"
}

# --- ensure .env with a real secret -------------------------------------------
$envPath = Join-Path $ProjectRoot $EnvFile
if (-not (Test-Path $envPath)) {
    Write-Step "no $EnvFile found - generating one with a random SECURITY_SECRET_KEY"
    $secret = New-RandomSecret
    "SECURITY_SECRET_KEY=$secret" | Out-File -FilePath $envPath -Encoding ascii
    Write-Ok "created $EnvFile (keep this file safe; it holds the token signing key)"
} else {
    $envContent = Get-Content $envPath -Raw
    if ($envContent -notmatch "SECURITY_SECRET_KEY=\S+") {
        Fail "$EnvFile exists but SECURITY_SECRET_KEY is missing or empty"
    }
}

# --- domain / HTTPS configuration ----------------------------------------------
$withProxy = -not [string]::IsNullOrWhiteSpace($Domain)
if ($withProxy) {
    if ([string]::IsNullOrWhiteSpace($AdminDomain)) { $AdminDomain = "admin.$Domain" }
    Write-Step "HTTPS enabled: $Domain (public) / $AdminDomain (admin) via Caddy"
    Set-EnvVar $envPath "DOMAIN" $Domain
    Set-EnvVar $envPath "ADMIN_DOMAIN" $AdminDomain
    if ($Target -eq "allinone") {
        # Both domains proxy to the single container.
        Set-EnvVar $envPath "UPSTREAM_FE" "allinone:8000"
        Set-EnvVar $envPath "UPSTREAM_ADMIN" "allinone:8000"
    } else {
        Set-EnvVar $envPath "UPSTREAM_FE" "fe:8000"
        Set-EnvVar $envPath "UPSTREAM_ADMIN" "admin:8000"
    }
}

$composeArgs = Resolve-ComposeArgs $Target (-not $NoBuild) $withProxy

# === LOCAL DEPLOY ==============================================================
if ([string]::IsNullOrWhiteSpace($Server)) {
    Write-Step "local deploy: target '$Target'"
    $dockerOk = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $dockerOk) { Fail "Docker is not installed or not on PATH" }

    Push-Location $ProjectRoot
    try {
        Write-Step "docker $($composeArgs -join ' ')"
        & docker @composeArgs
        if ($LASTEXITCODE -ne 0) { Fail "docker compose failed (exit $LASTEXITCODE)" }
        & docker compose ps
        if (-not $SkipHealthCheck) { Test-LocalHealth $Target }
        Write-Ok "local deploy of '$Target' finished"
        if ($withProxy) {
            Write-Ok "public:  https://$Domain"
            Write-Ok "admin:   https://$AdminDomain"
        }
    } finally {
        Pop-Location
    }
    exit 0
}

# === REMOTE DEPLOY (SSH) =======================================================
Write-Step "remote deploy: target '$Target' -> ${User}@${Server}:$RemotePath"
foreach ($tool in @("ssh", "scp", "tar")) {
    if ($null -eq (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Fail "'$tool' not found - install the Windows OpenSSH client (and tar, built into Win10+)"
    }
}
$sshDest = "$User@$Server"

# 1) package the project (source only - image is built on the server)
$bundle = Join-Path $env:TEMP "pywebfw-deploy.tar.gz"
Write-Step "packaging project -> $bundle"
if (Test-Path $bundle) { Remove-Item $bundle -Force }
tar -czf $bundle -C $ProjectRoot `
    --exclude ".venv" --exclude ".git" --exclude "data" --exclude ".env" `
    --exclude "__pycache__" --exclude ".pytest_cache" --exclude ".claude" .
if ($LASTEXITCODE -ne 0) { Fail "tar packaging failed" }

# 2) prepare remote dir, upload, extract
Write-Step "uploading to $sshDest"
ssh -p $SshPort $sshDest "mkdir -p '$RemotePath'"
if ($LASTEXITCODE -ne 0) { Fail "cannot reach $sshDest (check SSH key/agent)" }
scp -P $SshPort $bundle "${sshDest}:$RemotePath/deploy.tar.gz"
if ($LASTEXITCODE -ne 0) { Fail "scp upload failed" }
scp -P $SshPort $envPath "${sshDest}:$RemotePath/.env"
if ($LASTEXITCODE -ne 0) { Fail "scp .env upload failed" }
ssh -p $SshPort $sshDest "cd '$RemotePath' && tar -xzf deploy.tar.gz && rm deploy.tar.gz"
if ($LASTEXITCODE -ne 0) { Fail "remote extract failed" }

# 3) compose up on the server (remote shell is bash; && is fine there)
$remoteCompose = "docker " + ($composeArgs -join " ")
Write-Step "running on server: $remoteCompose"
ssh -p $SshPort $sshDest "cd '$RemotePath' && $remoteCompose && docker compose ps"
if ($LASTEXITCODE -ne 0) { Fail "remote docker compose failed" }

# 4) remote health check through SSH (ports may not be public)
if (-not $SkipHealthCheck) {
    $port = Get-HealthPort $Target
    if ($null -ne $port) {
        Write-Step "remote health check on container port $port"
        ssh -p $SshPort $sshDest "for i in 1 2 3 4 5 6; do curl -fsS http://localhost:$port/healthz && exit 0; sleep 5; done; exit 1"
        if ($LASTEXITCODE -ne 0) { Fail "remote health check failed" }
        Write-Ok "remote /healthz responded"
    }
}
Write-Ok "remote deploy of '$Target' to $Server finished"
if ($withProxy) {
    Write-Ok "public:  https://$Domain"
    Write-Ok "admin:   https://$AdminDomain"
    Write-Host " .. certificates are issued automatically on first request (DNS must point at $Server)" -ForegroundColor Yellow
}
