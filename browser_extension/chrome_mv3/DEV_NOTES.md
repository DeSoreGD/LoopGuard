# LoopGuard Browser Connector DEV Notes

The current MV3 extension is the local LoopGuard browser companion. It connects
to the local desktop app through Chrome Native Messaging, evaluates current tab
URLs locally, can redirect blocked tabs to extension-local `blocked.html`, can
install local Declarative Net Request session rules from the native host
snapshot, and has a YouTube-only content script for single-page route changes.

It does not use cloud services, telemetry, accounts, remote network APIs,
enterprise browser policy, silent install, or Chrome Web Store publishing. It
does not upload browsing history, full tab lists, page titles, DOM content,
cookies, form data, screenshots, or user data. Native Messaging messages stay
local between Chrome and the LoopGuard desktop app.

The native host name remains `com.selfboss.native_host` for compatibility with
existing desktop/native messaging protocol and installer registration.

## Chrome Web Store prep

P94A prepares metadata and packaging for future Chrome Web Store distribution.
It does not publish anything, create accounts, pay fees, submit forms, or add
enterprise force-install policy.

Package the extension ZIP from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\package_extension.ps1
```

Expected output:

```text
dist\extension\LoopGuardChromeExtension.zip
```

The ZIP should contain only extension files from this folder. It must not
contain `LoopGuard.exe`, `LoopGuardNativeHost.exe`, app databases, logs, user
profile data, tests, or build artifacts.

Chrome Web Store manual prep checklist:

1. Create/open the Chrome Web Store developer account manually.
2. Upload `dist\extension\LoopGuardChromeExtension.zip` manually.
3. Answer privacy practices from the inspected current behavior: local Native
   Messaging only, local URL evaluation, local DNR session rules, no telemetry,
   no cloud, no browsing history upload, no cookies/DOM/form/screenshot/page
   title collection.
4. Explain permissions:
   - `nativeMessaging`: local LoopGuard desktop app connection.
   - `tabs`: local tab URL evaluation for configured rules.
   - `alarms`: periodic local status polling.
   - `declarativeNetRequest`: local browser session rules.
   - YouTube content script matches: YouTube SPA route-change detection only.
   - `blocked.html`: local neutral blocked page.
5. Record the official Chrome Web Store extension ID after approval. No official
   Web Store ID exists in this repo yet unless the user provides one.
6. P94B should use the approved Web Store extension ID for installer external
   install metadata.

Known Web Store readiness blocker: icon assets are currently missing. Do not
add manifest `icons` until real extension icon files exist.

## Load Unpacked

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable Developer mode.
3. Choose **Load unpacked**.
4. Select `browser_extension/chrome_mv3`.

## Native Host

The native host name is `com.selfboss.native_host`. Registration is explicit:
use the LoopGuard installer optional task or the Settings Register/Repair
button for production, or use the helper in `packaging/native_messaging` for
development.

The Python development entrypoint is:

```powershell
.\.venv\Scripts\python -m selfboss_native_host
```

Chrome Native Messaging uses framed JSON over stdio, so manual terminal input is
not plain JSON. Use the unit-tested protocol helpers for local development.

`get_status` is minimal and read-only. It reports only current mode/access/day
and HIGH status fields, plus Safe/Recovery flags and
`browser_blocking: not_implemented`. It does not expose task lists, rules,
reward history, notes, browsing history, URLs, or browser blocking decisions.

`evaluate_url` is also local-only and evaluation-only. The extension may send
the current tab URL to the native host over Native Messaging, and the native
host returns a compact allow/block/unknown decision for developer inspection.
P39 does not redirect tabs, close tabs, add DNR rules, install content scripts,
implement YouTube/Shorts handling, or enforce URL/path blocking.

P40 adds state-change detection for development testing. The extension polls
`get_status` about every 30 seconds, compares a compact status signature, and
re-evaluates already-open `http` and `https` tabs when LoopGuard mode, access
level, HIGH, Safe Mode, Recovery Mode, or browser-blocking status changes. This
is still evaluation-only: tab sweep results are logged in the service worker
console, and no tab is redirected, closed, blocked, or modified.

P41 adds the first browser-level action. When the local native host returns
`decision: block` and `browser_blocking: active`, the extension redirects the
same tab to the local `blocked.html` page. Preview Only, Armed Dry Run, Safe
Mode, Recovery Mode, inactive-day, HIGH, and planned-use-pass allow states remain
log-only. P41 still does not add DNR rules, content scripts, URL/path special
handling, YouTube/Shorts handling, tab closing, network calls, analytics, or
remote services.

P41B keeps sweeping while browser blocking may be active, even when the status
signature has not changed. Already-open tabs should redirect within one polling
interval after HIGH or a planned-use pass expires. If Chrome's MV3 service
worker sleeps during development QA, reload the extension and repeat the check.

P42A adds trust/readiness status only. The background worker logs a minimal
browser integration status for this Chrome extension session and includes the
tab's `incognito` flag in local `evaluate_url` requests when Chrome provides it.
Browser HIGH safety is still Partial, not Trusted: Incognito is controlled only
if this extension is allowed in Incognito, and other browsers without the
LoopGuard extension are not browser-controlled. URLs are sent only to the local
native host over stdio; there is no telemetry, cloud, network service, localhost
HTTP API, DNR policy, content script, or browser policy control in P42A.

P42B adds a local desktop-visible heartbeat. The extension asks Chrome whether
Incognito access is allowed, then sends a `browser_heartbeat` message to the
native host on startup and during the existing status poll. The native host
writes only `browser_heartbeat.json` under the local LoopGuard data folder with
browser name, Incognito permission, browser-blocking readiness, and heartbeat
time. It does not store URLs, tabs, page titles, browsing history, tasks, rules,
rewards, notes, cookies, form input, or screenshots. Desktop Dashboard/Settings
can show Connected, Stale, or Disconnected, but Browser HIGH safety is still
Partial at best. Other browsers and Incognito without the extension remain
escape surfaces until a later user-approved hardening step.

P42E makes the local blocked page available as the redirect target for normal
and Incognito `http` and `https` tabs. The manifest exposes only
`blocked.html` as a web-accessible resource and uses split Incognito mode. It
does not add DNR rules, content scripts, browser policies, external network
requests, telemetry, or new native-host behavior. Incognito tabs are controlled
only when Chrome's **Allow in Incognito** setting is enabled for this extension.

P42F makes the split Incognito service worker initialize its own native-host
connection, status polling alarm, heartbeat, tab event handlers, and open-tab
sweeps. Regular and Incognito contexts log separately. Already-open Incognito
tabs should redirect after HIGH or a planned-use pass expires without requiring
reload, as long as **Allow in Incognito** is enabled and the Incognito extension
context is awake.

P43 adds Declarative Net Request session rules as a block-only hardening layer.
The extension requests a minimal blocked-domain snapshot from the local native
host and installs bounded `main_frame` session rules with `action: block`.
It intentionally does not add broad host permissions, so DNR does not redirect
to `blocked.html`; Chrome may show `ERR_BLOCKED_BY_CLIENT` for first-load DNR
blocks. The existing `evaluate_url` activation/navigation/sweep path remains
responsible for redirecting tabs to the LoopGuard blocked page when it can.

P44 adds native-host URL/path classification for browser evaluation responses.
The first hardcoded path policy is YouTube Shorts: `youtube.com`,
`www.youtube.com`, and `m.youtube.com` paths beginning with `/shorts` are
classified as `youtube_shorts` and can block through the existing
`evaluate_url` redirect/sweep path. This does not add content scripts, SPA route
watching, URL/path rule storage, DNR path rules, host permissions, browser
policies, or external network behavior. YouTube single-page-app route changes
that do not trigger tab URL update/navigation events remain a later task.

P45 adds a YouTube-only content script for SPA route-change detection. It runs
only on `youtube.com` pages, tracks `window.location.href`, and sends only the
current URL to the background worker with reason `youtube_spa_route_change`.
The background worker reuses the existing local native-host `evaluate_url`
path and redirect handler. P45 does not read page content, titles, comments,
recommendations, cookies, form input, storage, screenshots, or browsing
history, and it does not add broad host permissions, non-YouTube content
scripts, DNR path rules, network calls, telemetry, or new native-host behavior.

P46 improves the local blocked page as a return point. The page shows only
compact block metadata such as host, access level, URL family, path kind, and a
short reason; it does not display the full original URL. The **Open LoopGuard**
button sends a local Native Messaging request that can only ask the native host
to focus/show an already-running LoopGuard desktop window. It does not start
HIGH, complete tasks, edit rules, mutate rewards, change settings, launch a new
desktop instance, send network traffic, or expose task/rule/reward/history data
to the extension.

P47 removes hardcoded YouTube Shorts blocking. Browser path blocking now comes
from normal LoopGuard website rules such as `youtube.com/shorts/*` or
`reddit.com/r/all/*`. These path rules are evaluated only by the local browser
extension/native-host `evaluate_url` path. Hosts blocking and DNR snapshots
remain domain-only for now, and there are no DNR path rules, broad host
permissions, extra content scripts, or external network behavior.

P47B canonicalizes browser path rules. A user can enter `youtube.com/shorts`,
`youtube.com/shorts/`, or `youtube.com/shorts/*`; LoopGuard stores and treats
them as `youtube.com/shorts/*`. Bare-domain path rules also cover the matching
`www.` host, so `youtube.com/shorts/*` matches real
`www.youtube.com/shorts/...` URLs. Mobile or other subdomains still need an
explicit rule such as `m.youtube.com/shorts/*` or a wildcard host rule.

P48 logs minimal local browser attempts from `evaluate_url` into LoopGuard
access attempts. It stores hostname, matched rule target, scope, URL family,
path kind, access level, mode, decision, action, and reason code only. It does
not store full URLs, query strings, page titles, page content, cookies, form
values, screenshots, browsing history, or telemetry. Duplicate browser attempts
for the same host/rule/decision/access level are rate-limited to reduce
YouTube SPA route-change spam.

P49 expands the local browser heartbeat with diagnostics only. The extension
sends compact DNR status, active session rule count, Incognito permission, and
whether the YouTube SPA detector has been seen. The native host stores those
fields in the existing local heartbeat JSON for Settings. It does not store
URLs, domain lists, tabs, task/rule/reward data, page titles, cookies, page
content, browsing history, telemetry, or network data.

## State-Change Sweep QA

1. Reload the unpacked extension.
2. Open the extension service worker console.
3. Open a few `http` or `https` tabs.
4. Confirm the initial sweep logs `LoopGuard URL evaluation` decisions.
5. Change LoopGuard state, such as starting or ending HIGH or switching
   enforcement mode.
6. Within the polling interval, confirm the console logs a changed LoopGuard
   native host status and re-evaluates open tabs with `state_change_sweep`.
7. In Preview Only or Armed Dry Run, confirm blocked decisions are logged but
   tabs are not redirected.
8. In Real Hosts Blocking or Full Enforcement with an active day and a blocked
   site rule, confirm a blocked decision redirects the tab to extension-local
   `blocked.html`.
9. Open a blocked site during HIGH or a planned-use pass, then end HIGH or let
   the pass expire. Confirm the already-open tab redirects within one polling
   interval without reload or tab activation.
10. For Incognito QA, enable **Allow in Incognito** on the extension details
    page. If Chrome resets that setting after a manifest reload, enable it
    again before testing.
11. Open the service worker console. Chrome may show regular and Incognito
    extension context logs separately; look for `[regular]` and `[incognito]`
    labels.
12. In normal Chrome and Incognito, start HIGH, open a site that is normally
    blocked, end HIGH, and confirm both tabs redirect to LoopGuard `blocked.html`
    instead of Chrome's `ERR_BLOCKED_BY_CLIENT` page.
13. For the Incognito split-context sweep check, start HIGH, open the site in
    Incognito, do not reload or activate the tab, end HIGH, and wait one polling
    interval. The Incognito tab should redirect to LoopGuard `blocked.html`.
14. If Chrome's MV3 service worker sleeps during development QA, reload the
    extension and repeat the Incognito check.
15. For DNR QA, reload the extension after the manifest permission change. In
    Real Hosts Blocking or Full Enforcement, open a blocked domain in a new tab
    and confirm Chrome blocks the first navigation before page load. This may
    show Chrome's `ERR_BLOCKED_BY_CLIENT` page because P43 uses block-only DNR.
16. Start HIGH or an active planned-use pass and confirm the DNR block clears so
    the site can load when website release is trusted. End HIGH/pass and confirm
    DNR blocks return within one polling interval.
17. Confirm no tabs are closed and no non-YouTube content-script, policy,
    network, or telemetry behavior appears.
18. For YouTube SPA QA, reload the extension after the manifest change and
    re-enable **Allow in Incognito** if Chrome resets it. In LOW or MEDIUM
    access with Real Hosts Blocking or Full Enforcement active, open YouTube
    and navigate inside the page to `/shorts` without a full reload. The tab
    should redirect to LoopGuard `blocked.html`.
19. Start trusted HIGH and repeat the same YouTube Shorts in-page navigation.
    Shorts should be allowed while trusted HIGH is active.
20. End HIGH and confirm the existing sweep/redirect behavior blocks Shorts
    again. Switch to Preview Only or Armed Dry Run and confirm Shorts decisions
    may log, but tabs are not redirected.
21. For blocked-page QA, trigger LoopGuard `blocked.html` in normal Chrome and
    Incognito. Confirm the page shows host, access level, URL family, path kind,
    and reason with neutral language and no full original URL.
22. Click **Open LoopGuard**. If the desktop window is already running, the
    native host should request focus/show. If it is not discoverable, the page
    should show a compact failure reason. Confirm no LoopGuard state changes
    occur and no external network requests are made.
23. For browser attempt logging QA, with Full Enforcement active and a
    `youtube.com/shorts/*` HIGH rule, open Shorts in LOW or MEDIUM and confirm
    the local attempts view shows a browser attempt for `youtube.com`,
    `youtube_shorts`, and a path rule without a full URL or query string.
24. Navigate within YouTube several times and confirm duplicate route changes do
    not create excessive rows. Start HIGH, open Shorts, and confirm allowed
    attempts do not create block spam.
25. For path rule normalization QA, add only `youtube.com/shorts` as a HIGH
    website rule and confirm the Rules page stores/displays
    `youtube.com/shorts/*`. In LOW or MEDIUM, open
    `https://www.youtube.com/shorts/...` and confirm it blocks. Add
    `reddit.com/r/all` and confirm `https://www.reddit.com/r/all/` follows the
    same browser path rule behavior.
26. For browser diagnostics QA, reload the extension, re-enable **Allow in
    Incognito** if Chrome resets it, and open Settings. Confirm Extension,
    Native Messaging, Incognito, DNR, YouTube SPA detector, Last heartbeat, and
    Next action lines update.
27. Open YouTube once to activate the SPA detector, then check Settings again.
    In Real Hosts Blocking or Full Enforcement with blocked domains, confirm
    DNR shows an active rule count. Inspect `browser_heartbeat.json` and confirm
    it contains only compact status fields, not URLs or domain lists.

## Personal Trial Readiness QA

Run this before a real 3-7 day personal trial. The Settings page checklist is
read-only and does not mark these steps complete.

Desktop:

1. Confirm Full Enforcement is selectable.
2. Configure a disposable process rule such as `notepad.exe` and confirm it
   closes softly when blocked.
3. Confirm protected system and LoopGuard processes are not touched.
4. Confirm the LoopGuard managed hosts section applies and removes cleanly.
5. Confirm Safe Mode and Recovery Mode clear or avoid worsening managed state.

Browser:

1. Confirm the extension is connected in Settings.
2. Confirm **Allow in Incognito** is enabled and reported as allowed.
3. Confirm DNR is supported and active when blocked domains exist.
4. Add `youtube.com/shorts/*` as a HIGH website rule and confirm Shorts blocks
   in LOW or MEDIUM.
5. Confirm trusted HIGH allows the same Shorts URL.
6. End HIGH and confirm already-open normal Chrome tabs redirect or block again.
7. Repeat the HIGH-end check in Incognito.
8. Navigate inside YouTube to Shorts without reload and confirm the SPA detector
   path blocks.
9. Confirm the Dashboard browser summary updates after a browser block.
10. Confirm Settings browser diagnostics update after reload, Incognito, DNR,
    and YouTube checks.

Privacy:

1. Confirm Dashboard/log summaries show only hostname, rule, and classification
   metadata, not full URLs or query strings.
2. Confirm no telemetry, cloud, localhost API, or external network behavior is
   added by the checklist.
3. Do not treat the checklist as production-ready or tamper-proof certification.

## Development Registration

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable Developer mode.
3. Load unpacked `browser_extension/chrome_mv3`.
4. Copy the extension ID shown by the browser.
5. Preview the registration:

```powershell
.\.venv\Scripts\python packaging\native_messaging\register_native_host.py --browser chrome --extension-id EXTENSION_ID
```

6. Install the HKCU Native Messaging registration:

```powershell
.\.venv\Scripts\python packaging\native_messaging\register_native_host.py --browser chrome --extension-id EXTENSION_ID --install
```

7. Reload the extension and inspect the service worker console for the native
   host handshake result.
8. Unregister when finished:

```powershell
.\.venv\Scripts\python packaging\native_messaging\register_native_host.py --browser chrome --uninstall
```

Use `--browser edge` for Edge. The helper writes only the current user's HKCU
Native Messaging key when `--install` or `--uninstall` is explicit. The default
is dry-run and does not write registry keys.

P37B does not install the extension silently, implement blocking, redirect tabs,
add DNR rules, inspect URLs, send network requests, or make the extension a
source of truth for LoopGuard state. Production install should use a separate,
user-approved extension install and native-host registration flow.
