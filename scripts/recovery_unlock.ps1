$ErrorActionPreference = "Stop"

python -m selfboss.platform.recovery @args unlock --force-safe-mode
exit $LASTEXITCODE
