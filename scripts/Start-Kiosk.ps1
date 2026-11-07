$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimePath = Join-Path $ProjectRoot '.runtime'
$StatePath = Join-Path $RuntimePath 'kiosk.json'

try { Invoke-RestMethod 'http://127.0.0.1:8000/api/kiosk/status' -TimeoutSec 3 | Out-Null }
catch { throw 'Backend indisponível. Execute scripts/Start-Backend.ps1 primeiro.' }

$EdgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
)
$EdgePath = $EdgeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $EdgePath) { throw 'Microsoft Edge não foi encontrado.' }
New-Item -ItemType Directory -Force -Path $RuntimePath | Out-Null

if (Test-Path -LiteralPath $StatePath) {
    $Previous = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $Existing = Get-CimInstance Win32_Process -Filter "ProcessId = $($Previous.pid)" -ErrorAction SilentlyContinue
    if ($Existing -and $Existing.ExecutablePath -eq $EdgePath -and $Existing.CommandLine -match '--kiosk') {
        Write-Host 'Kiosk do Racing Manager já está ativo.'
        exit 0
    }
    Remove-Item -LiteralPath $StatePath -Force
}

$Url = 'http://localhost:8000/kiosk'
$ProfilePath = Join-Path $RuntimePath 'edge-profile'
$ProfileArgument = '--user-data-dir="' + $ProfilePath + '"'
$Arguments = @('--kiosk', $Url, '--edge-kiosk-type=fullscreen', '--no-first-run', '--no-default-browser-check', $ProfileArgument)
$Process = Start-Process -FilePath $EdgePath -ArgumentList $Arguments -WindowStyle Hidden -PassThru
@{ pid = $Process.Id; executable = $EdgePath; url = $Url; profile = $ProfilePath; started = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
Write-Host 'Kiosk iniciado em tela cheia. Para manutenção, use Alt+F4 no teclado conectado ao simulador.'
