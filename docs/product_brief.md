# Product Brief

## Summary

LoopGuard is a Windows-only, local-first desktop app that connects useful work to controlled recreation access. The user defines their own tasks, rules, reward thresholds, and recovery path.

## Alpha principles

- Local PySide6 desktop app with SQLite persistence.
- No cloud, telemetry, accounts, or AI service inside the app.
- Neutral, non-shaming product language.
- Test Mode locked on for the public alpha.
- Safe Mode and GUI-independent recovery always available.
- Explicit, removable local browser integration.
- No hidden startup, service, anti-kill, or enterprise browser policy.

## Current product surface

- daily MAIN and supporting task planning;
- focus sessions and completion tracking;
- earned reward minutes and timed recreation passes;
- local website/application rules and access decisions;
- staged enforcement readiness and diagnostics;
- experimental Chrome extension/native host integration;
- recovery status, unlock, and safe-mode commands.

## Safety requirements

- Every system-level action must be explicit, bounded, and reversible.
- Hosts changes must affect only the LoopGuard-managed marker section.
- Protected Windows and LoopGuard processes must never be termination targets.
- Safe Mode and Recovery Mode must override enforcement.
- Tests must not touch real hosts, processes, firewall, browser registration, or administrator settings.
- Antivirus detections are release blockers, never reasons to recommend exclusions or bypasses.
