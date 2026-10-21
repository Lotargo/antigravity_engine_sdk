Push-Location (Join-Path $PSScriptRoot '..')
try {
    go run ./testbench @args
    if ($LASTEXITCODE -ne 0) { throw "Go testbench failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}
