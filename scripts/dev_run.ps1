$ErrorActionPreference = "Stop"

$env:LOOPGUARD_TEST_MODE = "true"
$env:LOOPGUARD_SAFE_MODE = "true"
$env:PYTHONPATH = Join-Path (Get-Location) "src"

python -m selfboss.main
exit $LASTEXITCODE
