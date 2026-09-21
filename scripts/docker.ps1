[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentFile = Join-Path $projectRoot '.env.docker'
if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw 'Create .env.docker from .env.docker.example and set the secrets first.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found. Install Docker Desktop and open it first.'
}
$composeArguments = @(
    'compose', '--project-directory', $projectRoot,
    '--env-file', $environmentFile, '-f', (Join-Path $projectRoot 'compose.yaml')
)
switch ($Action) {
    'start'   { $composeArguments += @('up', '-d', '--wait') }
    'stop'    { $composeArguments += @('--profile', 'maintenance', 'stop') }
    'restart' { $composeArguments += @('restart', 'web') }
    'status'  { $composeArguments += @('--profile', 'maintenance', 'ps', '-a') }
    'logs'    { $composeArguments += @('logs', '--tail', '100', '-f', 'web') }
}
& docker @composeArguments
exit $LASTEXITCODE
