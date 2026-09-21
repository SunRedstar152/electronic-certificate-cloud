[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('run', 'status', 'verify')]
    [string]$Action = 'status',
    [Parameter(Position = 1)]
    [string]$BackupSet
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentFile = Join-Path $projectRoot '.env.docker'
if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw 'Create .env.docker from .env.docker.example and set the secrets first.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found. Open Docker Desktop first.'
}
$composeArguments = @('compose', '--project-directory', $projectRoot,
    '--env-file', $environmentFile, '-f', (Join-Path $projectRoot 'compose.yaml'))

function Invoke-Docker {
    param([string[]]$Arguments)
    $result = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed (exit $LASTEXITCODE)."
    }
    return $result
}

if ($Action -eq 'run') {
    Invoke-Docker ($composeArguments + @('run', '--rm', '--no-deps', 'backup', 'run-once'))
    exit 0
}
if ($Action -eq 'status') {
    & docker @composeArguments exec -T backup python3 /backup-tools/backup.py status
    exit $LASTEXITCODE
}

# Read resolved mount/image settings without displaying Compose's secret values.
$configuration = (Invoke-Docker ($composeArguments + @('config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
$backupMount = @($configuration.services.backup.volumes | Where-Object { $_.target -eq '/backups' })
if ($backupMount.Count -ne 1 -or $backupMount[0].type -ne 'bind') {
    throw 'The backup service must have exactly one bind mount at /backups.'
}
$backupRoot = (Resolve-Path -LiteralPath $backupMount[0].source).Path
if (-not $BackupSet) {
    $candidate = Get-ChildItem -LiteralPath $backupRoot -Directory |
        Where-Object { $_.Name -match '^auto-\d{8}T\d{6}Z-[0-9a-f]{8}$' -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json') -PathType Leaf) } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $candidate) { throw 'No completed automatic backup was found. Run backup.ps1 run first.' }
    $BackupSet = $candidate.Name
}
if ($BackupSet -notmatch '^auto-\d{8}T\d{6}Z-[0-9a-f]{8}$') {
    throw 'Pass a backup set directory name, not a path.'
}
$setPath = (Resolve-Path -LiteralPath (Join-Path $backupRoot $BackupSet)).Path
if ((Split-Path -Parent $setPath) -ne $backupRoot -or
    ((Get-Item -LiteralPath $setPath).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Backup set must be a real directory directly inside the configured backup directory.'
}
$verificationName = 'pkru-restore-check-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$verificationLabel = 'pkru.restore-check=' + $verificationName
try {
    # No published ports, no app network, no permanent data volume/database.
    Invoke-Docker @('run', '--detach', '--rm', '--name', $verificationName,
        '--label', $verificationLabel, '--network', 'none',
        '--tmpfs', '/var/lib/postgresql:rw',
        '--env', 'POSTGRES_DB=pkru_certificate', '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
        'postgres:18-alpine') | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker exec $verificationName pg_isready -h 127.0.0.1 -U postgres -d pkru_certificate *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Disposable PostgreSQL did not become ready in 60 seconds.' }
    $report = Invoke-Docker @('run', '--rm', '--network', "container:$verificationName",
        '--mount', "type=bind,source=$setPath,target=/backup-set,readonly",
        '--env', 'PGHOST=127.0.0.1', '--env', 'PGUSER=postgres', '--env', 'PGDATABASE=pkru_certificate',
        '--env', 'BACKUP_DISPOSABLE_VERIFY=1',
        $configuration.services.backup.image, 'verify-restored', '/backup-set')
    $verified = ($report -join "`n") | ConvertFrom-Json
    if ($verified.method -ne 'disposable-postgresql-18-restore') { throw 'Invalid restoration verification response.' }
    $reportPath = Join-Path $setPath 'restore-verification.json'
    $temporaryReport = Join-Path $setPath 'restore-verification.json.tmp'
    [IO.File]::WriteAllText($temporaryReport, (($verified | ConvertTo-Json -Depth 10) + "`n"), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryReport -Destination $reportPath -Force
    $verified | ConvertTo-Json -Depth 10
}
finally {
    # Check even if docker run failed after creating the container.
    $labelJson = & docker inspect --format '{{json .Config.Labels}}' $verificationName 2>$null
    if ($LASTEXITCODE -eq 0) {
        $actualLabel = ($labelJson | ConvertFrom-Json).'pkru.restore-check'
        if ($actualLabel -eq $verificationName) {
            Invoke-Docker @('rm', '--force', '--volumes', $verificationName) | Out-Null
        } else {
            throw "Could not confirm cleanup ownership of $verificationName; inspect Docker manually."
        }
    }
}
