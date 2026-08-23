# Security Checklist

LoopGuard must be strict enough to interrupt procrastination, but never dangerous.

## Local-Only Boundary

- No cloud sync.
- No backend.
- No web stack.
- No Docker, Electron, Tauri, or mobile code.
- SQLite is the only persistence layer.
- Browser URL decisions are evaluated by a local native host.
- No full URL, browsing-history, page-title, DOM, cookie, form, screenshot, or tab-list storage.

## Blocker Safety

- Every blocker action must be reversible.
- Every system-level change must be marker-based.
- Hosts changes must use `# SELF-BOSS BEGIN` and `# SELF-BOSS END`.
- Hosts recovery must preserve unrelated entries.
- Process blocking must support dry-run diagnostics.
- Test Mode is locked on in the public alpha.
- Firewall enforcement remains a non-mutating stub.

## Test Mode And Recovery

- Test mode is mandatory during development.
- Safe mode must disable real blockers on next launch.
- Recovery must work without the GUI.
- Recovery scripts must call the Python recovery entrypoint.
- Recovery files, recovery tests, and safe-mode logic must never be removed or weakened.

## Tests Must Not

- Touch the real Windows hosts file.
- Kill real user processes.
- Change firewall rules.
- Change startup settings.
- Install or configure services.
- Install browser integrations.
- Require administrator rights.

## Browser And Native Host

- Chrome integration must remain local, explicit, and removable.
- No enterprise browser policy or forced extension installation.
- Native Messaging registration is allowed only through a user-confirmed flow.
- Extension and native-host identifiers must match before packaging.
- No external network client or telemetry may be added.

## Release Check

- `pytest -q` passes.
- Recovery tests pass.
- PyInstaller onedir build succeeds.
- Recovery status and unlock scripts are documented.
- Build artifact is windowed and does not open a console window.
- Temporary databases, lock files, and machine-specific generated manifests are absent.
- Unsigned binaries are not published while an antivirus detection is unresolved.
- No Defender exclusion, antivirus bypass, obfuscation, or packed payload is used.
