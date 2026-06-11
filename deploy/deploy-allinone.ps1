# Deploy single-server mode: every module in ONE container (port 8000).
# Usage:  .\deploy-allinone.ps1                (local)
#         .\deploy-allinone.ps1 -Server web01.example.com -User ubuntu
& "$PSScriptRoot\deploy.ps1" -Target allinone @args
