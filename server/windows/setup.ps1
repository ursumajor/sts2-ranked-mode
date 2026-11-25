# One-time setup on Windows: creates server\.venv, installs requirements, and creates server\.env
# with a random admin key (if .env doesn't exist yet).
#
#   powershell -ExecutionPolicy Bypass -File server\windows\setup.ps1

$ErrorActionPreference = "Stop"
$server = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $server

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
}
& .venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    $key = & .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
    (Get-Content ".env.example") -replace "^RANKED_ADMIN_KEY=.*", "RANKED_ADMIN_KEY=$key" |
        Set-Content ".env" -Encoding utf8
    Write-Host "Created server\.env. Your admin key (for https://<host>/admin) is:" -ForegroundColor Green
    Write-Host "  $key"
} else {
    Write-Host "server\.env already exists; left unchanged."
}

Write-Host ""
Write-Host "Test it:   .venv\Scripts\python.exe -m ranked_server"
Write-Host "Then open: http://127.0.0.1:8000/api/v1/status"
Write-Host "Run on boot (as admin): powershell -ExecutionPolicy Bypass -File server\windows\install-task.ps1"
