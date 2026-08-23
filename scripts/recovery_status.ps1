$ErrorActionPreference = "Stop"

python -m selfboss.platform.recovery @args status
exit $LASTEXITCODE
