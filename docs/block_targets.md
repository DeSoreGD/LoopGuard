# Block Targets — LoopGuard MVP

This file defines default examples of websites, apps, and categories that LoopGuard may block.

The user must be able to edit these lists inside the app later.

---

## High-dopamine websites

These should usually be blocked in LOW mode.

- youtube.com
- www.youtube.com
- m.youtube.com
- music.youtube.com
- instagram.com
- www.instagram.com
- tiktok.com
- www.tiktok.com
- reddit.com
- www.reddit.com
- x.com
- twitter.com
- facebook.com
- twitch.tv
- www.twitch.tv
- netflix.com
- www.netflix.com
- 9gag.com
- pinterest.com

---

## Short-form video / reels / shorts

These are especially dangerous for procrastination.

- youtube.com/shorts
- instagram.com/reels
- tiktok.com
- facebook.com/reel
- reddit.com/r/popular
- reddit.com/r/all

Note: exact URL-level blocking may require a browser extension later. In the first MVP, domain-level blocking is acceptable.

---

## Games and launchers

These should usually be blocked in LOW mode and allowed only in HIGH mode.

- steam.exe
- steamwebhelper.exe
- epicgameslauncher.exe
- riotclientservices.exe
- leagueclient.exe
- valorant.exe
- battle.net.exe
- minecraftlauncher.exe
- robloxplayerbeta.exe
- discord.exe, optional depending on user settings

---

## Messengers

These may be blocked in LOW mode but allowed in MEDIUM mode.

- telegram.exe
- discord.exe
- whatsapp.exe
- signal.exe
- viber.exe
- messenger.com
- web.telegram.org
- discord.com
- web.whatsapp.com

---

## Music

Music may be allowed in MEDIUM mode depending on user preference.

- spotify.exe
- spotify.com
- music.youtube.com
- soundcloud.com

Recommended MVP rule:
- Spotify can be MEDIUM.
- YouTube Music should be blocked by default because it can easily lead to YouTube browsing.

---

## Useful websites

These should usually stay allowed.

- google.com
- wikipedia.org
- stackoverflow.com
- github.com
- docs.python.org
- pypi.org
- developer.mozilla.org
- learn.microsoft.com
- doc.qt.io
- openai.com
- chatgpt.com
- translate.google.com
- deepl.com

---

## Allowed task-specific URLs

The user should be able to allow specific URLs for specific tasks.

Examples:

- A specific YouTube tutorial video
- A documentation page
- A course lesson
- A GitHub issue
- A Stack Overflow answer
- A school or university resource

Important rule:
General YouTube may stay blocked while one specific educational YouTube URL is allowed for a task.

---

## MVP limitation

For the first version, it is acceptable to support:

- domain blocking through hosts file
- app blocking by process name
- simple allowed URL list in app logic
- test mode for all blocking behavior

Advanced URL-level blocking should be delayed until a browser extension is added later.