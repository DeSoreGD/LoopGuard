# LoopGuard

LoopGuard is an experimental, Windows-only desktop app for turning focused work into controlled access to distracting or high-stimulation activities.

The app is local-first: tasks, rules, timers, and activity state stay in a local SQLite database. There is no account system, cloud backend, telemetry, advertising SDK, or AI service inside the app.

> [!IMPORTANT]
> This repository is an early `0.1.0` alpha for source review and development testing. No signed Windows installer is published yet. Do not disable or bypass antivirus protection to run an unsigned build.

## Alpha status

The current alpha includes:

- daily task planning with a MAIN task and supporting tasks;
- focus sessions, reward-minute accounting, and timed recreation passes;
- local website and application rules;
- a PySide6 desktop UI, tray integration, and local SQLite persistence;
- Safe Mode and GUI-independent recovery commands;
- an experimental Chrome extension and native messaging host for local browser integration;
- a Windows PyInstaller build and Inno Setup packaging configuration;
- automated unit, integration, recovery, UI-smoke, and packaging tests.

Desktop process and hosts-file enforcement code is present, but **Test Mode is locked on in this alpha**, so normal source and packaged launches do not perform real desktop enforcement. Firewall enforcement remains a non-mutating stub. Browser integration is experimental and requires explicit local setup; there is no forced extension installation or browser policy.

## Safety model

LoopGuard is intended to interrupt procrastination without hiding control from the user.

- Safe Mode and Recovery Mode take precedence over enforcement.
- Recovery works without the GUI.
- Managed hosts-file changes are marker-based and reversible.
- Process handling uses an explicit protected-process allowlist.
- Browser/native-host setup is local, explicit, and removable.
- Tests use temporary files and injected process runners; they do not edit the real Windows hosts file, kill real processes, change firewall rules, or request administrator access.

See [Security Checklist](docs/security_checklist.md) and [Recovery Runbook](docs/recovery_runbook.md).

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- PowerShell

## Run from source

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e . -r requirements-dev.txt
.\scripts\dev_run.ps1
```

The development launcher forces Test Mode and Safe Mode.

Run the automated checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Development build

Create an unsigned Windows onedir build with:

```powershell
.\scripts\build.ps1 -Clean
```

Expected output:

```text
dist\LoopGuard\LoopGuard.exe
```

The build is for local development testing. Because it is unsigned and built with PyInstaller, antivirus products may classify it incorrectly. Treat any detection as a release blocker: verify the source and build pipeline, submit a false-positive report to the antivirus vendor when appropriate, and never instruct users to add exclusions or bypass security warnings.

## Browser integration

The Chrome MV3 extension and native host live under `browser_extension/chrome_mv3/` and `src/selfboss_native_host/`. They communicate locally through Chrome Native Messaging. URL evaluation stays local, and the app does not store full browsing history, page content, cookies, screenshots, form data, or tab lists.

The extension is not distributed through the Chrome Web Store in this alpha. Developer setup is manual and should be used only with the matching locally built native host.

## Project layout

```text
src/selfboss/                  Desktop app
src/selfboss_native_host/      Local Chrome native host
browser_extension/chrome_mv3/  Experimental Chrome extension
packaging/                     PyInstaller and installer configuration
scripts/                       Development, build, and recovery commands
tests/                         Automated test suite
docs/                          Architecture and safety documentation
```

The Python package and native-host identifier retain the earlier internal `selfboss` name for compatibility; the public product name is LoopGuard.

## Known limitations

- early alpha with no stability or compatibility guarantee;
- Windows only;
- Test Mode is locked on, so desktop enforcement is not enabled for general users;
- browser integration requires manual developer setup;
- builds are unsigned and are not published as GitHub Releases yet;
- no automatic updater, cloud sync, mobile app, or multi-device support.

## License

LoopGuard is licensed under the [GNU General Public License v3.0](LICENSE).
