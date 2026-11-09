<#
.SYNOPSIS
    Indexes the library with each configuration in experiments\configs, one after another.

.DESCRIPTION
    Each experiment lives in %LOCALAPPDATA%\recall-exp\<name> with its own
    config.json and index: the production index (%LOCALAPPDATA%\recall) is not
    touched. Runs serially because they all share the GPU. Progress goes to
    <home>\index.log and a summary to %LOCALAPPDATA%\recall-exp\runs.log.

.EXAMPLE
    pwsh -File experiments\run.ps1                       # all
    pwsh -File experiments\run.ps1 -Only e3-gemma2       # one
    pwsh -File experiments\run.ps1 -LimitFiles 3 -Tag smoke   # short test run
#>
[CmdletBinding()]
param(
    [string[]]$Only,
    [int]$LimitFiles = 0,
    [string]$Tag = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root '.venv\Scripts\python.exe'
$base = Join-Path $env:LOCALAPPDATA 'recall-exp'
New-Item -ItemType Directory -Force $base | Out-Null
$summary = Join-Path $base 'runs.log'

foreach ($cfg in Get-ChildItem (Join-Path $PSScriptRoot 'configs') -Filter *.json | Sort-Object Name) {
    $name = $cfg.BaseName
    if ($Only -and $name -notin $Only) { continue }
    $home_ = Join-Path $base ($name + $(if ($Tag) { "-$Tag" } else { '' }))
    New-Item -ItemType Directory -Force $home_ | Out-Null
    Copy-Item $cfg.FullName (Join-Path $home_ 'config.json') -Force
    $env:RECALL_HOME = $home_
    $args_ = @('-I', '-m', 'recall', 'index')
    if ($LimitFiles -gt 0) { $args_ += @('--limit-files', "$LimitFiles") }
    $t = Get-Date
    & $py @args_ 2> (Join-Path $home_ 'index.log') > (Join-Path $home_ 'report.json')
    $rc = $LASTEXITCODE
    $min = [math]::Round(((Get-Date) - $t).TotalMinutes, 1)
    $rep = try { Get-Content (Join-Path $home_ 'report.json') -Raw | ConvertFrom-Json } catch { $null }
    $line = '{0} {1} rc={2} min={3} chunks={4} errors={5}' -f (Get-Date -Format s), (Split-Path $home_ -Leaf), $rc, $min, $rep.total_chunks, $rep.errors.Count
    Add-Content $summary $line -Encoding utf8
    Write-Output $line
}
Remove-Item Env:RECALL_HOME -ErrorAction SilentlyContinue
