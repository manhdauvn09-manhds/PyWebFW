<#
.SYNOPSIS
    One-time setup: store a GitHub token securely and push both repos.

.DESCRIPTION
    - Prompts for the token with hidden input (never echoed, never written
      to disk or command history).
    - Replaces the credential stored for github.com in Windows Credential
      Manager (encrypted by Windows, same store git already uses).
    - Pushes the full private monorepo to pywebfw-pro and the clean
      community snapshot (+ tag v0.2.0) to PyWebFW.

.NOTES
    Run from the repository root:  .\tools\setup_github_push.ps1
    The token can be revoked anytime at https://github.com/settings/tokens
#>
[CmdletBinding()]
param(
    [string]$GitHubUser = "manhdauvn09-manhds"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host " OK $Message" -ForegroundColor Green }
function Fail([string]$Message)       { Write-Host " !! $Message" -ForegroundColor Red; exit 1 }

# --- 1) read the token with hidden input --------------------------------------
Write-Host "Paste your GitHub token (input is hidden, press Enter when done):"
$secure = Read-Host -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ([string]::IsNullOrWhiteSpace($token)) { Fail "no token entered" }
if ($token -notmatch '^(ghp_|github_pat_)') {
    Write-Host " .. warning: token does not look like a GitHub token (ghp_/github_pat_)" -ForegroundColor Yellow
}

# --- 2) replace the stored github.com credential -------------------------------
Write-Step "updating Windows Credential Manager entry for github.com"
"protocol=https`nhost=github.com`n" | git credential reject 2>$null
"protocol=https`nhost=github.com`nusername=$GitHubUser`npassword=$token`n" | git credential approve
$token = $null   # drop the in-memory copy as soon as it is stored

# --- 3) push the private monorepo ----------------------------------------------
Write-Step "pushing full monorepo -> pywebfw-pro (private)"
git push private master:main
if ($LASTEXITCODE -ne 0) { Fail "private push failed (check token permissions: Contents + Workflows, both repos)" }
Write-Ok "private: https://github.com/$GitHubUser/pywebfw-pro"

# --- 4) push the community snapshot --------------------------------------------
Write-Step "pushing community edition v0.2.0 -> PyWebFW (public)"
Push-Location dist\community
git push -u origin main --tags
$publicExit = $LASTEXITCODE
Pop-Location
if ($publicExit -ne 0) { Fail "public push failed" }
Write-Ok "public:  https://github.com/$GitHubUser/PyWebFW (tag v0.2.0)"

# --- 5) verify ------------------------------------------------------------------
Write-Step "verifying remote branches"
git ls-remote --heads private | ForEach-Object { Write-Host "  pro:    $_" }
Push-Location dist\community
git ls-remote --heads origin | ForEach-Object { Write-Host "  public: $_" }
Pop-Location
Write-Ok "all done - CI will run automatically on both repos"
