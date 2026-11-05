# Manual foreground launcher only. No installer, scheduled task or policy bypass.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$StateDirectory,
    [ValidateSet('init','run','stop','status')][string]$Action = 'status',
    [switch]$StartStopped
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows is required; this host is not validated.' }
# IsPathRooted also accepts C:relative and \root-relative: neither is safe here.
$FullyQualifiedLocalPath = '^[A-Za-z]:[\\/]'
if ($PythonExe -notmatch $FullyQualifiedLocalPath -or $StateDirectory -notmatch $FullyQualifiedLocalPath) {
    throw 'Use fully qualified local drive paths, such as C:\Folder\file. Relative and UNC paths are not accepted.'
}
# Capture both before Push-Location so validation and use refer to the same path.
$PythonPath = [IO.Path]::GetFullPath($PythonExe)
$StateDirectoryPath = [IO.Path]::GetFullPath($StateDirectory)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Supply an absolute path to an already installed, trusted python.exe.'
}
if ($StartStopped -and $Action -ne 'run') { throw 'StartStopped requires Action run.' }
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Push-Location -LiteralPath $Root
try {
    $Arguments = @('-I','-B',(Join-Path $PSScriptRoot 'run_offline.py'),$Action,'--state-dir',$StateDirectoryPath)
    if ($StartStopped) { $Arguments += '--start-stopped' }
    & $PythonPath @Arguments
    $Result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Result
