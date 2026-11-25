# Pull the latest code, update packages, and restart the scheduled task. Run from an Administrator
# PowerShell (needed to restart a SYSTEM task):
#
#   powershell -ExecutionPolicy Bypass -File server\windows\update.ps1

$ErrorActionPreference = "Stop"
$taskName = "STS2 Ranked Server"
$server = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location (Split-Path -Parent $server)

git pull
& (Join-Path $server ".venv\Scripts\python.exe") -m pip install -r (Join-Path $server "requirements.txt") | Out-Null

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/status" -TimeoutSec 10 | Out-Null
        Write-Host "Updated and restarted." -ForegroundColor Green
    } catch {
        Write-Warning "Restarted, but the server didn't answer yet. Check server\server.log."
    }
} else {
    Write-Host "Updated. No scheduled task found, so restart the server yourself."
}
