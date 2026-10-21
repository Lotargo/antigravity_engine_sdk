# Run the native test stand with its local browser UI.
# Use run.ps1 --ui for an offline mock UI instead.
& (Join-Path $PSScriptRoot 'run.ps1') --ui --live @args
