# Recovery Runbook

LoopGuard recovery must work without the GUI.

Recovery only touches LoopGuard-managed state:

- Hosts entries between `# SELF-BOSS BEGIN` and `# SELF-BOSS END`
- LoopGuard hosts backup files
- Local LoopGuard safe-mode and test-mode flag files

Recovery does not kill processes, change firewall rules, modify startup or service settings, install browser integrations, flush DNS, or request administrator rights.

## Check Recovery Status

```powershell
.\scripts\recovery_status.ps1
```

This prints:

- app home path
- hosts file path
- backup path
- whether LoopGuard hosts markers are present
- whether a LoopGuard backup exists
- whether safe mode is forced
- whether test mode is forced

## Unlock And Force Safe Mode

```powershell
.\scripts\recovery_unlock.ps1
```

This runs the Python recovery manager without starting the GUI.

Behavior:

1. If a LoopGuard hosts backup exists, restore it.
2. If no backup exists, remove only the block between LoopGuard markers.
3. Preserve unrelated hosts content.
4. Write `safe_mode.flag` under the LoopGuard app home so the next launch starts in safe mode.

If the real Windows hosts file requires administrator rights, run the same command from an elevated PowerShell window.

## Reset Test Mode

```powershell
.\scripts\reset_test_mode.ps1
```

This writes local `test_mode.flag` and `safe_mode.flag` files so the next launch is non-enforcing.

## Test With Temporary Files

Use temporary paths when testing recovery commands:

```powershell
$tmp = New-Item -ItemType Directory -Force "$env:TEMP\LoopGuard-recovery-test"
$hosts = Join-Path $tmp "hosts"
$backup = Join-Path $tmp "hosts.LoopGuard.bak"
"127.0.0.1 localhost" | Set-Content -Encoding UTF8 $hosts

.\scripts\recovery_status.ps1 --app-home $tmp --hosts-path $hosts --backup-path $backup
.\scripts\recovery_unlock.ps1 --app-home $tmp --hosts-path $hosts --backup-path $backup
.\scripts\reset_test_mode.ps1 --app-home $tmp --hosts-path $hosts --backup-path $backup
```

These temp-mode commands do not touch the real Windows hosts file.
