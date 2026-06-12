# Deploy ONLY the public front-end container (port 8001).
# Usage:  .\deploy-fe.ps1                      (local)
#         .\deploy-fe.ps1 -Server 10.0.0.21 -User deploy
& "$PSScriptRoot\deploy.ps1" -Target fe @args
