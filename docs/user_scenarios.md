# User Scenarios — LoopGuard MVP

LoopGuard is a Windows desktop application that helps users get out of procrastination by turning productive work into access to high-dopamine activities.

The main idea: the user becomes their own boss. They define tasks, rules, and rewards. The app helps enforce those rules locally.

---

## Scenario 1 — Starting a focused day

### User goal

The user wants to start the day without instantly falling into YouTube, Instagram, games, or other distracting apps.

### Flow

1. User opens LoopGuard.
2. Dashboard shows today's state: LOW access.
3. User sees today's tasks.
4. Distracting websites and apps are blocked.
5. User can still use useful websites like Google, Wikipedia, documentation, educational resources, and work tools.
6. User starts working on one task.

### Expected result

The user is not forced aggressively, but the easiest path becomes doing the task instead of procrastinating.

---

## Scenario 2 — Completing a task and earning reward time

### User goal

The user wants to get access to entertainment after doing something useful.

### Flow

1. User creates or selects a task.
2. User works on the task.
3. User presses "Mark as done".
4. App shows a short confirmation delay, for example 30–60 seconds.
5. After the delay, the task is completed.
6. User earns reward minutes.
7. Reward balance increases.
8. User can spend those minutes to enter HIGH mode.

### Expected result

The user connects effort with reward: "I did something useful, now I can access dopamine."

---

## Scenario 3 — Watching a YouTube guide for a task

### User goal

The user needs YouTube for a productive task, but YouTube is normally blocked.

### Flow

1. User creates a task like: "Watch Python tutorial about PySide6 layouts".
2. Task includes an allowed URL, for example a specific YouTube video link.
3. YouTube as a general website remains blocked.
4. The specific video URL is allowed for that task.
5. User can open only the allowed educational URL.
6. After finishing, user marks the task as done.

### Expected result

The app does not block useful learning, but it still prevents falling into endless recommendations, shorts, or unrelated videos.

---

## Scenario 4 — User tries to open a blocked app or site

### User goal

The user impulsively tries to open a distracting app or website.

### Flow

1. User opens a blocked game, app, or website.
2. LoopGuard detects the blocked target.
3. The app closes the blocked application or prevents the website from loading.
4. LoopGuard shows a simple message:
   "This is blocked in LOW mode. Complete a task to unlock reward time."
5. User returns to the task list.

### Expected result

The block is strict enough to interrupt the automatic procrastination loop, but the message stays calm and clear.

---

## Scenario 5 — User wants to surrender

### User goal

The user wants to disable restrictions because they feel bored, tired, or frustrated.

### Flow

1. User presses "Request Surrender".
2. LoopGuard does not unlock immediately.
3. App starts a surrender timer, for example 12 hours.
4. Until the timer ends, the user cannot fully disable restrictions.
5. User can still use useful tools and complete tasks.
6. After 12 hours, surrender becomes available.
7. User can disable the current day rules or recover access.

### Expected result

The surrender delay prevents impulsive self-sabotage, but still gives the user a safe way out later.

---

## Scenario 6 — Bad day mode

### User goal

The user has a low-energy day and cannot complete the normal plan.

### Flow

1. User opens LoopGuard.
2. User chooses "Bad Day Mode".
3. App reduces the difficulty of the day.
4. Required task count becomes lower.
5. Rewards may become smaller, but the day is not treated as a total failure.
6. User completes at least one small task.

### Expected result

The app avoids an all-or-nothing mindset. A bad day still becomes a small win.

---

## Scenario 7 — Recovery after a bug

### User goal

The app breaks or blocks something incorrectly, and the user needs to recover safely.

### Flow

1. User closes LoopGuard.
2. User runs the recovery script.
3. Recovery removes only LoopGuard-managed blocks.
4. Recovery does not remove unrelated system settings.
5. App starts in safe mode next time.
6. User can fix the configuration or report the bug.

### Expected result

The user never gets permanently locked out because of a bug.