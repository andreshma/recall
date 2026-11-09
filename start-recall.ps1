<#
.SYNOPSIS
    Starts the local recall service if it is not alive. Meant for SessionStart.

.DESCRIPTION
    1. If service.json points to a live process, that process proves it knows
       the token (GET /proof, an HMAC bound to its port) and /health answers
       with the token, it does nothing (silent). The token is never sent to a
       process that has not proved it, e.g. one that took over the port of a
       service that was killed.
    2. Otherwise it launches `pythonw -I -c ... serve` hidden and DETACHED from
       the session (via Win32_Process.Create, so it does not inherit the hook's
       job object and Claude Code does not kill it when the hook ends). It does
       not wait for the model to load: the session's first prompt may get no
       context (the hook fails open) and the following ones do.
    3. If there is no index, it starts nothing and notes it in start.log.

    ALWAYS exits 0 and writes nothing to stdout: a failure here must never break
    or clutter session startup. Details go to
    %LOCALAPPDATA%\recall\start.log.

    Works in Windows PowerShell 5.1 and in pwsh 7: HTTP goes through
    HttpWebRequest with no proxy (Invoke-RestMethod -NoProxy does not exist
    in 5.1).

.NOTES
    Does not touch any other hook or service.
#>
[CmdletBinding()]
param(
    [string]$RecallRoot = $PSScriptRoot,
    [string]$StateDir = $(if ($env:RECALL_HOME) { $env:RECALL_HOME } else { Join-Path $env:LOCALAPPDATA 'recall' }),
    [double]$HealthTimeoutSec = 1.0
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 leaves $PSScriptRoot empty in param() default
# values; pwsh does not. With this fallback the script works in both.
if (-not $RecallRoot) { $RecallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
$log = Join-Path $StateDir 'start.log'

function Write-RecallLog([string]$msg) {
    try {
        if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
        # Rotation: over 1 MiB -> start.log.1 (a single copy; these are startup lines).
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.1" -Force }
        Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format s), $msg) -Encoding utf8
    } catch { }
}

function Invoke-RecallGet([int]$Port, [string]$PathAndQuery, [string]$Token) {
    # GET on loopback without any proxy. Returns @{ Code; Body } for any HTTP
    # status; throws only if nothing answered (refused, timeout).
    $req = [System.Net.HttpWebRequest]::Create(('http://127.0.0.1:{0}{1}' -f $Port, $PathAndQuery))
    $req.Proxy = $null
    $req.Method = 'GET'
    $req.KeepAlive = $false
    $req.Timeout = [int]($HealthTimeoutSec * 1000)
    $req.ReadWriteTimeout = [int]($HealthTimeoutSec * 1000)
    if ($Token) { $req.Headers['Authorization'] = 'Bearer ' + $Token }
    $resp = $null
    try {
        $resp = $req.GetResponse()
    } catch {
        # PowerShell may wrap the WebException; an HTTP error still carries a response.
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [System.Net.WebException])) { $ex = $ex.InnerException }
        if (-not $ex -or -not $ex.Response) { throw }
        $resp = $ex.Response
    }
    try {
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        return @{ Code = [int]$resp.StatusCode; Body = $reader.ReadToEnd() }
    } finally {
        $resp.Close()
    }
}

function Get-RecallProof([string]$Token, [string]$Nonce, [int]$Port) {
    # Same as recall.service.server_proof: HMAC-SHA256(token, "recall-proof:<nonce>:<port>").
    $key = [System.Text.Encoding]::ASCII.GetBytes($Token)
    $hmac = New-Object System.Security.Cryptography.HMACSHA256 -ArgumentList (, $key)
    try {
        $mac = $hmac.ComputeHash([System.Text.Encoding]::ASCII.GetBytes(('recall-proof:{0}:{1}' -f $Nonce, $Port)))
    } finally {
        $hmac.Dispose()
    }
    return (($mac | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Test-SameText([string]$a, [string]$b) {
    # Comparison that does not stop at the first different character.
    if ($a.Length -ne $b.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $a.Length; $i++) { $diff = $diff -bor ([int][char]$a[$i] -bxor [int][char]$b[$i]) }
    return ($diff -eq 0)
}

function Test-RecallHealthy {
    # $true: healthy. $false: launch a new one. $null: alive but not answering.
    $svcFile = Join-Path $StateDir 'service.json'
    if (-not (Test-Path $svcFile)) { return $false }
    try {
        $svc = Get-Content $svcFile -Raw | ConvertFrom-Json
        $svcPid = [int]$svc.pid
        $port = [int]$svc.port
        $token = [string]$svc.token
    } catch {
        return $false
    }
    if ($port -lt 1 -or $port -gt 65535 -or $svcPid -lt 1 -or -not $token) { return $false }
    $proc = Get-Process -Id $svcPid -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    # Pid reused by another program after a reboot: the service is not alive.
    if ($proc.ProcessName -notmatch '^python') { return $false }
    try {
        $nonce = [Guid]::NewGuid().ToString('N')
        $r = Invoke-RecallGet -Port $port -PathAndQuery ('/proof?nonce={0}' -f $nonce)
        $proof = $null
        if ($r.Code -eq 200) { try { $proof = [string]($r.Body | ConvertFrom-Json).proof } catch { } }
        if (-not $proof -or -not (Test-SameText $proof (Get-RecallProof $token $nonce $port))) {
            # Whatever holds the port does not know the token: service.json is
            # stale. Nothing secret was sent to it and it is not killed.
            Write-RecallLog ("port {0} did not prove the token (HTTP {1}); service.json is stale" -f $port, $r.Code)
            Remove-Item $svcFile -Force -ErrorAction SilentlyContinue
            return $false
        }
        $h = Invoke-RecallGet -Port $port -PathAndQuery '/health' -Token $token
        $body = $null
        try { $body = $h.Body | ConvertFrom-Json } catch { }
        if ($h.Code -eq 200 -and $body -and $body.ok -eq $true) { return $true }
        if ($h.Code -eq 503) {
            # The service gave up (unrecoverable engine) and is exiting. Kill it
            # in case it lingers, but only the pid that the proven server reports.
            if ($body -and [int]$body.pid -eq $svcPid) {
                Write-RecallLog ("service degraded (503, pid {0}); restarting it" -f $svcPid)
                Stop-Process -Id $svcPid -Force -ErrorAction SilentlyContinue
            } else {
                Write-RecallLog ("service degraded (503) but it reports a pid other than {0}; not killing it" -f $svcPid)
            }
            Remove-Item $svcFile -Force -ErrorAction SilentlyContinue
            return $false
        }
        Write-RecallLog ("unexpected /health answer (HTTP {0})" -f $h.Code)
        return $null
    } catch {
        # Process alive but not answering: it may be loading the model. Do not
        # launch another; `recall serve` itself exits if one is already alive.
        return $null
    }
}

try {
    $state = Test-RecallHealthy
    if ($state -eq $true) { exit 0 }
    if ($null -eq $state) { Write-RecallLog 'service alive but not answering /health; not launching another'; exit 0 }

    $db = Join-Path $StateDir 'index.sqlite3'
    if (-not (Test-Path $db)) { Write-RecallLog "no index at $db; run 'recall index'"; exit 0 }

    $pyw = Join-Path $RecallRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $pyw)) { Write-RecallLog "missing $pyw"; exit 0 }

    # Win32_Process.Create starts the process with the registry environment, not
    # this one: a RECALL_HOME set for this script would not reach the service,
    # which would then serve (and write service.json into) the default state
    # dir. So the state dir is passed as an argument and set before recall is
    # imported. The recall package sets HF_HUB_OFFLINE=1 itself on import.
    $stateFull = [System.IO.Path]::GetFullPath($StateDir)
    # A trailing backslash would escape the closing quote (MSVCRT rules): double it.
    $stateArg = $stateFull -replace '(\\+)$', '$1$1'
    $code = "import os,sys;os.environ['RECALL_HOME']=sys.argv[1];from recall.cli import main;sys.exit(main(['serve']))"
    $cmd = '"{0}" -I -c "{1}" "{2}"' -f $pyw, $code, $stateArg
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine      = $cmd
        CurrentDirectory = $RecallRoot
    }
    Write-RecallLog ("launched recall serve (pid {0}, rc {1}, state {2})" -f $r.ProcessId, $r.ReturnValue, $stateFull)
} catch {
    Write-RecallLog ("error: {0}" -f $_.Exception.Message)
}
exit 0
