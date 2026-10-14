[CmdletBinding()]
param(
    [int]$Jobs = 0
)

# Builds the assemblers the kit ships (cc65's ca65 and ld65, the AS macro
# assembler, p2bin) for Windows x86-64 and Linux x86-64 in Docker, from the AS
# source archive in core/toolchains/sources and cc65 and p2bin at the commits
# tools/toolchains/build.sh pins, into core/toolchains/payloads. The payloads
# are not tracked: run this once in a checkout, and before packaging the
# studio. The build is reproducible, so the result must match the digests in
# core/toolchains/manifest.json; `rltool build-tools status` checks.

$ErrorActionPreference = "Stop"

$kit = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolchains = Join-Path $kit "src\retrolarium_kit\core\toolchains"
$sources = Join-Path $toolchains "sources"
$payloads = Join-Path $toolchains "payloads"
New-Item -ItemType Directory -Force -Path $payloads | Out-Null

$image = "retrolarium-toolchains"
# Docker reports progress on stderr, which Windows PowerShell turns into errors
# when the output is redirected; only the exit code says whether it failed.
function Invoke-Docker {
    $ErrorActionPreference = "Continue"
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "docker $($args[0]) failed with exit code $LASTEXITCODE" }
}

Invoke-Docker build -t $image (Join-Path $PSScriptRoot "toolchains")
$arguments = @("run", "--rm", "-v", "${sources}:/sources:ro", "-v", "${payloads}:/out")
if ($Jobs -gt 0) { $arguments += @("-e", "JOBS=$Jobs") }
Invoke-Docker @arguments $image
Write-Host "Wrote the assemblers to $payloads"
