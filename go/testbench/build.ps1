$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $taskBenchBin = Join-Path $PSScriptRoot 'bin'
    New-Item -ItemType Directory -Force -Path $taskBenchBin | Out-Null
    go build -trimpath -o (Join-Path $taskBenchBin 'antigravity-testbench.exe') ./testbench
    if ($LASTEXITCODE -ne 0) { throw "Go build failed (exit $LASTEXITCODE)" }
    Write-Output (Join-Path $taskBenchBin 'antigravity-testbench.exe')
} finally {
    Pop-Location
}
