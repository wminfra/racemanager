$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimePath = Join-Path $ProjectRoot '.runtime'
$PythonwPath = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$StatePath = Join-Path $RuntimePath 'overlay.json'

if (-not (Test-Path -LiteralPath $PythonwPath)) { throw 'pythonw.exe ausente. Reinstale as dependências do ambiente virtual.' }
New-Item -ItemType Directory -Force -Path $RuntimePath | Out-Null

if (Test-Path -LiteralPath $StatePath) {
    $Previous = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $Existing = Get-CimInstance Win32_Process -Filter "ProcessId = $($Previous.pid)" -ErrorAction SilentlyContinue
    if ($Existing -and $Existing.Name -eq 'pythonw.exe' -and
        $Existing.ExecutablePath -eq $PythonwPath -and $Existing.CommandLine -match 'app\.modules\.overlay') {
        Write-Host 'Overlay do Racing Manager já está ativo.'
        exit 0
    }
    Remove-Item -LiteralPath $StatePath -Force
}

$LogPath = Join-Path $RuntimePath 'overlay.log'
$ErrorPath = Join-Path $RuntimePath 'overlay-error.log'
$Process = Start-Process -FilePath $PythonwPath -ArgumentList @('-m', 'app.modules.overlay') `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $LogPath `
    -RedirectStandardError $ErrorPath -PassThru
@{ pid = $Process.Id; executable = $PythonwPath; module = 'app.modules.overlay';
   started = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
Start-Sleep -Milliseconds 500
$Started = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)" -ErrorAction SilentlyContinue
if (-not $Started -or $Started.ExecutablePath -ne $PythonwPath -or $Started.CommandLine -notmatch 'app\.modules\.overlay') {
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    throw "Overlay não permaneceu ativo. Consulte $ErrorPath."
}
Write-Host 'Overlay nativo iniciado. Ele fica oculto até uma sessão ACC estar em andamento.'
