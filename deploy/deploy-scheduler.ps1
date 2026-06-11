# Deploy ONLY the scheduler/cron container (no public port).
# Usage:  .\deploy-scheduler.ps1               (local)
#         .\deploy-scheduler.ps1 -Server 10.0.0.23 -User deploy
& "$PSScriptRoot\deploy.ps1" -Target scheduler @args
