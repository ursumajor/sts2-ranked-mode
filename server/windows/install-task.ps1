# Registers a Windows scheduled task that starts the ranked server at boot (no login needed) and
# restarts it if it crashes. Output goes to server\server.log. Run from an *Administrator* PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File server\windows\install-task.ps1
#
# Manage it afterwards:
#   Start-ScheduledTask   -TaskName "STS2 Ranked Server"
#   Stop-ScheduledTask    -TaskName "STS2 Ranked Server"
#   Get-ScheduledTask     -TaskName "STS2 Ranked Server" | Get-ScheduledTaskInfo
#   Unregister-ScheduledTask -TaskName "STS2 Ranked Server" -Confirm:$false

$ErrorActionPreference = "Stop"
$taskName = "STS2 Ranked Server"
$server = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $server ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) { throw "Run server\windows\setup.ps1 first ($python not found)." }
if (-not (Test-Path (Join-Path $server ".env"))) { throw "server\.env is missing. Run setup.ps1 first." }

$action = New-ScheduledTaskAction -Execute $python -Argument "-m ranked_server --log-file server.log" -WorkingDirectory $server
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "STS2 Ranked Mode server (python -m ranked_server)" -Force | Out-Null

# If the server is already running by hand on port 8000, stop that first or the task can't bind the port.
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/status" -TimeoutSec 10
    Write-Host "Task '$taskName' is running. Server time: $($status.server_time)" -ForegroundColor Green
} catch {
    Write-Warning "Task registered, but the server didn't answer yet. Check server\server.log."
}
