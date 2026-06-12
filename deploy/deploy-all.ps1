# Deploy split mode: fe (8001) + admin (8002) + scheduler, on one Docker host.
# Usage:  .\deploy-all.ps1                     (local)
#         .\deploy-all.ps1 -Server 10.0.0.20 -User deploy
& "$PSScriptRoot\deploy.ps1" -Target all @args
