param(
    [ValidateSet('Launch', 'Diagnose')][string]$Action = 'Diagnose',
    [string]$GameDirectory = 'D:\Renegade AP Testing\TT-4.8.4'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$testDirectory = (Resolve-Path -LiteralPath $GameDirectory).Path
$gameExecutable = Join-Path $testDirectory 'game.exe'
$runtimeExecutable = Join-Path $testDirectory 'game2.exe'
if (!(Test-Path -LiteralPath (Join-Path $testDirectory 'ttversion.txt')) -or
    !(Test-Path -LiteralPath $runtimeExecutable)) {
    throw 'This folder does not contain a TT test installation.'
}
if ($Action -eq 'Launch') {
    Start-Process -FilePath $gameExecutable -WorkingDirectory $testDirectory -WindowStyle Hidden
    exit 0
}
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$probeScript = Join-Path $PSScriptRoot 'probe_game_build.py'
$outputFile = Join-Path $projectDirectory '.research\tt-4.8.4\latest-diagnostic.json'
$probeArguments = @($probeScript, '--game-dir', $testDirectory, '--output', $outputFile)
$matchingProcesses = @(Get-Process -Name 'game2' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path -eq $runtimeExecutable })
if ($matchingProcesses.Count -gt 1) {
    throw 'More than one TT test process is running. Close extra copies before diagnosing.'
}
if ($matchingProcesses.Count -eq 1) {
    $probeArguments += @('--pid', [string]$matchingProcesses[0].Id)
} else {
    Write-Host 'No readable TT process found; collecting an on-disk report only.'
}
& $pythonExecutable @probeArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Read-only report saved to $outputFile"
Write-Host 'This tool never attaches AP hooks. TT gameplay support is still being developed.'
