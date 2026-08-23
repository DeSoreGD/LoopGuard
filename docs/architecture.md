# Architecture

## Repository shape

LoopGuard uses a Python `src` layout:

- `src/selfboss/` contains the PySide6 desktop application;
- `src/selfboss_native_host/` contains the local Chrome Native Messaging host;
- `browser_extension/chrome_mv3/` contains the experimental browser extension;
- `tests/` contains unit, integration, recovery, and UI-smoke tests;
- `packaging/` contains PyInstaller, native-messaging, and installer configuration;
- `docs/` contains product, privacy, safety, and recovery notes.

The desktop entry point is `selfboss.main`. Application state is stored locally in SQLite through repositories under `src/selfboss/data/`.

## Main layers

- UI: PySide6 Widgets.
- Core: domain models, state transitions, reward accounting, and use cases.
- Data: SQLite schema and repositories.
- Platform: process, hosts, recovery, browser-setup, and test-mode adapters.
- Browser: Chrome MV3 extension plus a local stdio native host.
- Packaging: Windows onedir application and optional Inno Setup installer.

## Safety boundary

Test Mode is locked on in the current alpha. The process and hosts adapters contain real-action implementations, but the shipped application keeps their effective behavior in dry-run mode. Firewall enforcement remains a stub.

Safe Mode and Recovery Mode override enforcement. Recovery can run without the GUI and only removes LoopGuard-managed state. Tests use temporary paths and injected runners rather than modifying the real Windows system.

## Privacy boundary

There is no cloud service, telemetry, account system, or external application API. Browser URL decisions are evaluated locally. The app does not persist full URLs, browsing history, page titles, DOM content, cookies, form data, screenshots, or tab lists.
