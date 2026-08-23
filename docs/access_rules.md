# Access Rules — LOW / MEDIUM / HIGH

LoopGuard uses access levels to control what the user can access during the day.

The goal is not to punish the user. The goal is to make productive work the easiest path to reward.

---

## Core access levels

LoopGuard has three main access levels:

1. LOW
2. MEDIUM
3. HIGH

The app starts the day in LOW mode by default.

---

## LOW mode

LOW mode is the default focus mode.

### Purpose

LOW mode blocks high-dopamine distractions and keeps the user close to useful work.

### Allowed

- Task list
- Notes
- Google search
- Wikipedia
- Documentation
- Educational websites
- Work tools
- Code editors
- Local files
- Task-specific allowed URLs

### Blocked

- YouTube
- Instagram
- TikTok
- Reels
- Shorts
- Games
- Game launchers
- Entertainment websites
- Infinite-scroll websites
- Optional: messengers
- Optional: music websites

### Product behavior

When the user tries to access a blocked target, LoopGuard should show a calm message:

> This is blocked in LOW mode. Complete a task to unlock reward time.

---

## MEDIUM mode

MEDIUM mode is a limited access mode.

### Purpose

MEDIUM mode allows neutral activities that may be useful but can still become distracting.

### Allowed

- Everything from LOW mode
- Messengers, if enabled by user settings
- Music, if enabled by user settings
- Some neutral websites
- Communication tools
- Limited browsing

### Blocked

- YouTube entertainment
- Instagram
- TikTok
- Games
- Shorts/Reels
- High-dopamine sites

### How to unlock MEDIUM

Possible MVP rule:

- MEDIUM unlocks after completing at least one main task.
- Or user can configure MEDIUM to be available by default.

Recommended MVP default:

- MEDIUM unlocks after one meaningful task is completed.

---

## HIGH mode

HIGH mode is reward mode.

### Purpose

HIGH mode gives access to entertainment as a reward for completed work.

### Allowed

- YouTube
- Instagram
- Games
- Entertainment websites
- User-defined high-dopamine apps
- Other blocked distractions

### Limit

HIGH mode uses earned reward minutes.

Example:

- Complete small task: +5 minutes
- Complete normal task: +15 minutes
- Complete important task: +30 minutes

When HIGH time runs out, app returns to LOW or MEDIUM mode.

### Product behavior

The dashboard should show:

- current access level
- earned reward minutes
- active HIGH countdown
- next task suggestion

---

## Task reward rules

Each task can have a reward value.

Recommended default values:

| Task type | Reward |
|---|---:|
| Tiny task | 5 minutes |
| Normal task | 15 minutes |
| Important task | 30 minutes |
| Main daily task | unlocks MEDIUM + reward minutes |

The user should be able to edit reward values.

---

## Task completion confirmation

To reduce fake task completion, task completion should not be instant.

Recommended MVP behavior:

1. User clicks "Mark as done".
2. App shows confirmation delay.
3. User waits 30–60 seconds.
4. User confirms again.
5. Reward is granted.

This will not fully prevent cheating, but it creates friction against impulsive self-sabotage.

---

## Main task rule

A "main task" is the most important task of the day.

Recommended default:

- Completing one main task unlocks MEDIUM mode.
- HIGH mode still requires earned reward minutes.

This creates a simple daily structure:

1. Do the main task.
2. Unlock a more flexible day.
3. Earn entertainment time through more tasks.

---

## Bad day mode

Bad Day Mode reduces pressure without fully disabling the system.

Recommended MVP behavior:

- User can enable Bad Day Mode once per day.
- Main task requirement becomes easier.
- Reward values may be reduced.
- The day is still considered a partial success if at least one small task is completed.

Purpose:

- Avoid all-or-nothing failure.
- Keep the user moving even on low-energy days.

---

## Default daily flow

Recommended default flow:

1. App starts in LOW mode.
2. User completes one main task.
3. MEDIUM mode unlocks.
4. User completes more tasks.
5. User earns HIGH minutes.
6. User spends HIGH minutes.
7. When HIGH time ends, app returns to MEDIUM or LOW.