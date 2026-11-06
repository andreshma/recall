<#
.SYNOPSIS
    Arranca el servicio local de recall si no esta vivo. Pensado para SessionStart.

.DESCRIPTION
    1. Si service.json apunta a un proceso vivo y /health responde con el token,
       no hace nada (silencioso).
    2. Si no, lanza `pythonw -I -m recall serve` oculto y DESACOPLADO de la sesion
       (via Win32_Process.Create, para que no herede el job object del hook y
       Claude Code no lo mate al terminar el hook). No espera a que cargue el
       modelo: el primer prompt de la sesion puede no tener contexto (el hook
       falla abierto) y los siguientes ya si.
    3. Si no hay indice, no arranca nada y lo anota en start.log.

    SIEMPRE sale con 0 y no escribe en stdout: un fallo aqui nunca debe romper ni
    ensuciar el arranque de la sesion. Los detalles van a
    %LOCALAPPDATA%\recall\start.log.

.NOTES
    No toca ningun otro hook ni servicio.
#>
[CmdletBinding()]
param(
    [string]$RecallRoot = $PSScriptRoot,
    [string]$StateDir = $(if ($env:RECALL_HOME) { $env:RECALL_HOME } else { Join-Path $env:LOCALAPPDATA 'recall' }),
    [double]$HealthTimeoutSec = 1.0
)

$ErrorActionPreference = 'Stop'
$log = Join-Path $StateDir 'start.log'

function Write-RecallLog([string]$msg) {
    try {
        if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
        # Rotacion: mas de 1 MiB -> start.log.1 (una sola copia; son lineas de arranque).
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.1" -Force }
        Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format s), $msg) -Encoding utf8
    } catch { }
}

function Test-RecallHealthy {
    $svcFile = Join-Path $StateDir 'service.json'
    if (-not (Test-Path $svcFile)) { return $false }
    try {
        $svc = Get-Content $svcFile -Raw | ConvertFrom-Json
        if (-not (Get-Process -Id ([int]$svc.pid) -ErrorAction SilentlyContinue)) { return $false }
        $r = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $svc.port) `
            -Headers @{ Authorization = "Bearer $($svc.token)" } -TimeoutSec $HealthTimeoutSec -NoProxy
        return [bool]$r.ok
    } catch {
        # Proceso vivo pero sin responder: puede estar cargando el modelo. No
        # lanzar otro; el propio `recall serve` sale si ya hay uno vivo.
        return $null
    }
}

try {
    $state = Test-RecallHealthy
    if ($state -eq $true) { exit 0 }
    if ($null -eq $state) { Write-RecallLog 'servicio vivo sin responder a /health; no se lanza otro'; exit 0 }

    $db = Join-Path $StateDir 'index.sqlite3'
    if (-not (Test-Path $db)) { Write-RecallLog "sin indice en $db; corre 'recall index'"; exit 0 }

    $pyw = Join-Path $RecallRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $pyw)) { Write-RecallLog "no existe $pyw"; exit 0 }

    $cmd = '"{0}" -I -m recall serve' -f $pyw
    # Win32_Process.Create no hereda el entorno de este proceso; no hace falta:
    # el paquete recall fija HF_HUB_OFFLINE=1 al importarse y no trae
    # huggingface-hub instalado.
    $r =Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine      = $cmd
        CurrentDirectory = $RecallRoot
    }
    Write-RecallLog ("lanzado recall serve (pid {0}, rc {1})" -f $r.ProcessId, $r.ReturnValue)
} catch {
    Write-RecallLog ("error: {0}" -f $_.Exception.Message)
}
exit 0
