param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^/[A-Za-z0-9_./-]+$')]
    [string]$RemoteProjectRoot
)

$ErrorActionPreference = "Stop"
$ArchivePath = (Resolve-Path -LiteralPath $ArchivePath).Path
$remoteName = [IO.Path]::GetFileName($ArchivePath)
if ($remoteName -notmatch '^[A-Za-z0-9._-]+\.tar$') {
    throw "Archive name must contain only safe ASCII filename characters and end in .tar."
}
$payload = [IO.File]::ReadAllBytes($ArchivePath)
$encoded = [Convert]::ToBase64String($payload)
$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash.ToLowerInvariant()

if ($RemoteProjectRoot.Split('/') -contains '..') { throw 'Parent path segments are not allowed.' }
$projectRoot = $RemoteProjectRoot.TrimEnd('/')
if (-not $projectRoot) { throw 'A non-root project directory is required.' }
$remoteEncoded = "$projectRoot/upload/$remoteName.b64"
$remoteArchive = "$projectRoot/upload/$remoteName"
$chunkLength = 31000
$runScript = "/home/lyullee/.cache/nbcp-hangang/run.sh"

function Invoke-Hangang([string]$RemoteCommand) {
    & wsl.exe -d Ubuntu-22.04 -u lyullee --exec /bin/bash $runScript cpu $RemoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "The wrapped Hangang command failed with exit code $LASTEXITCODE. Inspect its output before retrying."
    }
}

# The preflight uses only the existing shared control connection. It never
# opens a new SSH transport or prompts for authentication.
Invoke-Hangang "hostname; whoami"
$remoteCount = Invoke-Hangang "if [ -f '$remoteEncoded' ]; then wc -c < '$remoteEncoded'; else printf '0\n'; fi" | Select-Object -Last 1
if ($remoteCount -notmatch '^\s*\d+\s*$') {
    throw "Unexpected remote upload offset: $remoteCount"
}
$offset = [int]$remoteCount.Trim()
if ($offset -gt $encoded.Length -or ($offset -ne $encoded.Length -and $offset % $chunkLength -ne 0)) {
    throw "Remote partial upload does not match a valid local chunk boundary. Remove $remoteEncoded after reconnecting."
}

for (; $offset -lt $encoded.Length; $offset += $chunkLength) {
    $count = [Math]::Min($chunkLength, $encoded.Length - $offset)
    $chunk = $encoded.Substring($offset, $count)
    Invoke-Hangang "printf '%s' '$chunk' >> '$remoteEncoded'"
    $percent = [Math]::Floor(100 * ($offset + $count) / $encoded.Length)
    Write-Progress -Activity "Uploading Hangang validation payload" -Status "$percent%" -PercentComplete $percent
}
Write-Progress -Activity "Uploading Hangang validation payload" -Completed

$finalize = "base64 -d '$remoteEncoded' > '$remoteArchive' && printf '%s  %s\n' '$sha256' '$remoteArchive' | sha256sum -c - && tar -tf '$remoteArchive' >/dev/null && tar -xf '$remoteArchive' -C '$projectRoot' && cd '$projectRoot' && sha256sum -c hangang-payloads.SHA256SUMS"
Invoke-Hangang $finalize
