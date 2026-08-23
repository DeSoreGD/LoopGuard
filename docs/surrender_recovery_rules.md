# Surrender and Recovery Rules

LoopGuard must be strict enough to stop impulsive procrastination, but safe enough that users are never permanently locked out.

This file defines the surrender and recovery logic.

---

## Difference between surrender and recovery

LoopGuard has two different escape systems:

1. Surrender
2. Recovery

They must not be mixed.

---

## Surrender

Surrender is a normal product feature.

It is for the situation where the user wants to give up on the current day rules.

Example:

> "I do not want to follow the plan anymore today."

Surrender should be delayed.

---

## Recovery

Recovery is a safety feature.

It is for bugs, broken configuration, failed blocking logic, or accidental lockout.

Example:

> "The app broke and I need to restore access."

Recovery must always exist.

Recovery must not be removed.

Recovery must not depend on the GUI working.

---

## Surrender rule

Recommended MVP rule:

- User can request surrender.
- Surrender does not unlock immediately.
- Default surrender delay: 12 hours.
- Until the delay ends, current restrictions remain active.
- After the delay ends, user can disable the current day's rules.

Purpose:

- Prevent impulsive self-sabotage.
- Make "giving up" possible, but not instant.
- Encourage the user to complete at least one task instead of waiting.

---

## Surrender flow

1. User presses "Request Surrender".
2. App shows warning:
   "Surrender will become available after 12 hours. Until then, restrictions remain active."
3. User confirms.
4. App stores surrender request time.
5. Dashboard shows surrender countdown.
6. Before 12 hours pass, surrender button is disabled.
7. After 12 hours pass, surrender becomes available.
8. User can disable the current day's restrictions.

---

## What surrender can do

After surrender delay ends, surrender may:

- stop the current day plan
- disable LOW/MEDIUM/HIGH restrictions for the day
- reset active HIGH countdown
- mark the day as surrendered in history

Surrender should not:

- delete tasks
- delete user settings
- delete block lists
- remove recovery tools
- change system files except through normal unblock logic

---

## Recovery rule

Recovery must be available even if the app UI is broken.

Recovery should:

- remove only LoopGuard-managed hosts entries
- restore LoopGuard backups if available
- disable active blockers
- enable safe mode for next launch
- write a recovery log/report

Recovery should not:

- remove unrelated hosts entries
- delete unrelated system settings
- delete user data
- permanently disable the app
- depend on the main GUI

---

## Safe mode

Safe mode is a special launch mode.

When safe mode is active:

- no real blocking is applied
- UI opens normally
- dashboard shows "SAFE MODE ACTIVE"
- user can inspect settings
- user can disable bad rules
- user can export logs
- user can turn safe mode off manually

Safe mode exists to prevent repeated lockout after a bug.

---

## Test mode

Test mode is for development.

When test mode is active:

- real hosts file is not changed
- real firewall rules are not changed
- real apps are not killed unless explicitly configured for testing
- UI shows "TEST MODE ACTIVE"
- blockers return dry-run reports

Test mode must be the default during development.

---

## Real enforcement rule

Real blocking should not be enabled until all of these are true:

- recovery scripts exist
- recovery tests pass
- hosts blocker uses markers
- hosts blocker has backup/restore logic
- process blocker has dry-run mode
- app shows test mode clearly
- app can start in safe mode

---

## Anti-sabotage rule

The app should make bypassing annoying, but not dangerous.

Allowed MVP anti-sabotage:

- delayed surrender
- close blocked apps
- block distracting domains
- tray app stays running
- optional start with Windows later
- optional settings lock later

Not allowed in MVP:

- irreversible system changes
- hiding recovery from the user
- permanent lock without exit
- blocking Windows settings completely
- preventing uninstall with no recovery path
- removing user's control over their own computer permanently

---

## Recommended default

For MVP:

- Surrender delay: 12 hours
- Recovery: always available
- Safe mode: always available
- Test mode: enabled by default during development
- Real blocking: only after recovery is tested