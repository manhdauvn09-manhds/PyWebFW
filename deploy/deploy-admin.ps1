# Deploy ONLY the admin container (port 8002).
# Usage:  .\deploy-admin.ps1                   (local)
#         .\deploy-admin.ps1 -Server 10.0.0.22 -User deploy
& "$PSScriptRoot\deploy.ps1" -Target admin @args
