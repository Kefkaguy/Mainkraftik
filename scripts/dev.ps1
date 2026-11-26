param([switch]$WithWorker)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
if(-not (Test-Path -LiteralPath (Join-Path $taskRoot '.env'))){throw 'Run scripts/bootstrap.py first'}
if(-not (Test-Path -LiteralPath $pythonPath)){throw 'Create .venv and install requirements.lock.txt first'}
New-Item -ItemType Directory -Path (Join-Path $taskRoot '.local') -Force | Out-Null
$processes = @()
if(-not (Get-NetTCPConnection -State Listen -LocalPort 5543 -ErrorAction SilentlyContinue)) {
  $processes += Start-Process -FilePath 'node' -ArgumentList (Join-Path $taskRoot 'scripts\local-db.mjs') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.local\postgres.log') -RedirectStandardError (Join-Path $taskRoot '.local\postgres.err')
  Start-Sleep -Seconds 3
}
Push-Location (Join-Path $taskRoot 'apps\api')
try { & $pythonPath -m intelligence.cli migrate; if($LASTEXITCODE -ne 0){throw 'Migration failed'} } finally { Pop-Location }
if(Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue){throw 'Port 8100 already in use; no process was stopped'}
$processes += Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','intelligence.main:app','--host','127.0.0.1','--port','8100' -WorkingDirectory (Join-Path $taskRoot 'apps\api') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.local\api.log') -RedirectStandardError (Join-Path $taskRoot '.local\api.err')
if(Get-NetTCPConnection -State Listen -LocalPort 3100 -ErrorAction SilentlyContinue){throw 'Port 3100 already in use; no process was stopped'}
$processes += Start-Process -FilePath 'node' -ArgumentList (Join-Path $taskRoot 'apps\web\node_modules\next\dist\bin\next'),'dev','--hostname','127.0.0.1','--port','3100' -WorkingDirectory (Join-Path $taskRoot 'apps\web') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.local\web.log') -RedirectStandardError (Join-Path $taskRoot '.local\web.err')
if($WithWorker){$processes += Start-Process -FilePath $pythonPath -ArgumentList '-m','intelligence.cli','worker' -WorkingDirectory (Join-Path $taskRoot 'apps\api') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.local\worker.log') -RedirectStandardError (Join-Path $taskRoot '.local\worker.err')}
$processes | Select-Object Id,ProcessName | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot '.local\started-processes.json')
Write-Output 'Local app: http://127.0.0.1:3100/intelligence. Credentials: .local/access.txt'
