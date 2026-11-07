$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$EnvPath = Join-Path $ProjectRoot '.env'
$RuntimePath = Join-Path $ProjectRoot '.runtime'
$StatePath = Join-Path $RuntimePath 'backend.json'

if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Ambiente Python ausente. Siga docs/INSTALL.md.' }
if (-not (Test-Path -LiteralPath $EnvPath)) { throw 'Arquivo .env ausente. Copie .env.example e configure uma chave secreta.' }
$SecretLine = Get-Content -LiteralPath $EnvPath | Where-Object { $_ -match '^RACING_SECRET_KEY\s*=\s*\S+' } | Select-Object -First 1
if (-not $SecretLine) { throw 'RACING_SECRET_KEY não foi configurada no .env.' }

New-Item -ItemType Directory -Force -Path $RuntimePath | Out-Null
if (Test-Path -LiteralPath $StatePath) {
    $Previous = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $Existing = Get-CimInstance Win32_Process -Filter "ProcessId = $($Previous.pid)" -ErrorAction SilentlyContinue
    if ($Existing -and $Existing.ExecutablePath -eq $PythonPath -and $Existing.CommandLine -match 'app\.main:app') {
        try { Invoke-RestMethod 'http://127.0.0.1:8000/api/kiosk/status' -TimeoutSec 2 | Out-Null; Write-Host 'Backend já está ativo.'; exit 0 } catch {}
    }
    Remove-Item -LiteralPath $StatePath -Force
}

$LogPath = Join-Path $RuntimePath 'backend.log'
$ErrorPath = Join-Path $RuntimePath 'backend-error.log'
$Process = Start-Process -FilePath $PythonPath -ArgumentList @('-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $LogPath -RedirectStandardError $ErrorPath -PassThru
@{ pid = $Process.Id; executable = $PythonPath; started = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8

$Ready = $false
for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
    Start-Sleep -Milliseconds 500
    try { Invoke-RestMethod 'http://127.0.0.1:8000/api/kiosk/status' -TimeoutSec 2 | Out-Null; $Ready = $true; break } catch {}
}
if (-not $Ready) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    throw "Backend não respondeu. Consulte $ErrorPath."
}
Write-Host 'Backend disponível em http://localhost:8000 (rede local, porta 8000).'
