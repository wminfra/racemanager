param([switch]$ShortSessionTestMode)
$ErrorActionPreference = 'Stop'
if ($ShortSessionTestMode) { & (Join-Path $PSScriptRoot 'Start-Backend.ps1') -ShortSessionTestMode }
else { & (Join-Path $PSScriptRoot 'Start-Backend.ps1') }
& (Join-Path $PSScriptRoot 'Start-Kiosk.ps1')
Write-Host 'Racing Manager pronto. Painel do operador: http://<IP-local-do-computador>:8000/operator'
