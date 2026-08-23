$ErrorActionPreference = "Stop"

python -m selfboss.platform.recovery @args reset-test-mode
exit $LASTEXITCODE
