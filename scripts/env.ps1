<#
.SYNOPSIS
    Enter the project's portable run environment for this shell session.

.DESCRIPTION
    Usage (dot-source it so the variables persist):

        . scripts\env.ps1

    The environment lives at .conda next to the project. Two Windows quirks are
    handled here instead of in application code:

    * PROJ cannot open proj.db from a non-ASCII directory, so PROJ_DATA/PROJ_LIB
      are left unset and PROJ/GDAL fall back to their own search, which works
      from any location. The package does the same when it detects a non-ASCII
      install path.
    * Nothing depends on the ambient PROJ_DATA (it may point at unrelated
      software), so it is cleared rather than inherited.

.PARAMETER Quiet
    Suppress the confirmation message.
#>
[CmdletBinding()]
param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$conda = Join-Path $root '.conda'
$python = Join-Path $conda 'python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing: $python`nRun: conda env create -f environment.yml --prefix `"$conda`""
}

foreach ($variable in 'PROJ_DATA', 'PROJ_LIB', 'GDAL_DRIVER_PATH') {
    Remove-Item -LiteralPath "Env:$variable" -ErrorAction SilentlyContinue
}

$env:GDAL_DATA = Join-Path $conda 'Library\share\gdal'
$env:PATH = (Join-Path $conda 'Library\bin') + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
if (-not $PSBoundParameters.ContainsKey('Quiet')) {
    Write-Host "GeoQuickView environment ready"
    Write-Host "  python : $python"
    Write-Host "  project: $root"
}