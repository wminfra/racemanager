$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimePath = Join-Path $ProjectRoot '.runtime'

try {
    $Status = Invoke-RestMethod 'http://127.0.0.1:8000/api/kiosk/status' -TimeoutSec 2
    if ($Status.session -and $Status.session.status -in @('waiting', 'running')) {
        throw 'Existe uma sessão aguardando orientações, inicialização ou em andamento. Encerre-a pelo painel do operador antes de parar o ambiente.'
    }
} catch {
    if ($_.Exception.Message -match 'Existe uma sessão') { throw }
}

$OverlayState = Join-Path $RuntimePath 'overlay.json'
if (Test-Path -LiteralPath $OverlayState) {
    $State = Get-Content -LiteralPath $OverlayState -Raw | ConvertFrom-Json
    $ExpectedPythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $($State.pid)" -ErrorAction SilentlyContinue
    if ($Process -and $Process.Name -eq 'pythonw.exe' -and $Process.ExecutablePath -eq $ExpectedPythonw -and
        $Process.CommandLine -match 'app\.modules\.overlay') {
        Stop-Process -Id ([int]$State.pid) -Force
    }
    Remove-Item -LiteralPath $OverlayState -Force
}

$KioskState = Join-Path $RuntimePath 'kiosk.json'
if (Test-Path -LiteralPath $KioskState) {
    $State = Get-Content -LiteralPath $KioskState -Raw | ConvertFrom-Json
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $($State.pid)" -ErrorAction SilentlyContinue
    if ($Process -and $Process.Name -eq 'msedge.exe' -and $Process.ExecutablePath -eq $State.executable -and $Process.CommandLine -match '--kiosk' -and $Process.CommandLine.Contains('/kiosk') -and $Process.CommandLine.Contains($State.profile)) {
        Stop-Process -Id ([int]$State.pid) -Force
    }
    Remove-Item -LiteralPath $KioskState -Force
}

$BackendState = Join-Path $RuntimePath 'backend.json'
if (Test-Path -LiteralPath $BackendState) {
    $State = Get-Content -LiteralPath $BackendState -Raw | ConvertFrom-Json
    $ExpectedPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $($State.pid)" -ErrorAction SilentlyContinue
    if ($Process -and $Process.Name -eq 'python.exe' -and $Process.ExecutablePath -eq $ExpectedPython -and $Process.CommandLine -match 'app\.main:app') {
        Stop-Process -Id ([int]$State.pid) -Force
    }
    Remove-Item -LiteralPath $BackendState -Force
}
Write-Host 'Ambiente iniciado por estes scripts foi encerrado. Overlay, Edge e backend foram parados; logs e banco foram preservados.'
