# Roadmap

150 improvements and suggestions. **Items 1-62 shipped in v2.3.0.** Items 63-150 are the backlog, grouped by theme and
roughly ordered by value inside each group.

## Shipped in v2.3.0

### Security and hardening
1. [x] Security headers on every page: Content-Security-Policy, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy.
2. [x] Session cookie is marked Secure automatically when the page is reached over HTTPS (including Tailscale serve).
3. [x] Weak and common passwords are refused.
4. [x] Admin token compared in constant time.
5. [x] "Sign out everywhere else" ends every other session of your account.
6. [x] Players' names are validated before they reach the console (blocks selectors and injection).
7. [x] Files over 2 MB cannot be saved through the editor.
8. [x] Starting a server is locked per server, so a double click or the scheduler cannot launch two copies.
9. [x] Creating a server requires accepting the Minecraft EULA (recorded in the server's metadata).
10. [x] Ctrl+C or closing the terminal saves and stops running servers instead of orphaning them (normal mode).
11. [x] Audit log export to CSV, safe against spreadsheet formula injection.
12. [x] Staff panel shows who is active right now (last seen).
13. [x] Activity log filter by person or action.

### Operations
14. [x] Public `/api/health` endpoint for uptime monitors.
15. [x] Diagnostics card: versions, Java, disk, uptime, mode, update state; one-click copy for support.
16. [x] Rotating `manager.log` (1 MB x 3) with a viewer in Settings.
17. [x] Disk usage per server and per server's backups, plus free space.
18. [x] Low disk alert to Discord (once a day).
19. [x] Sustained high CPU or memory alert (two minutes, with a cool-down).
20. [x] "Server running but not answering" alert after five silent minutes.
21. [x] Static files are versioned by content age, so a changed file is never served stale, and cached for a year otherwise.
22. [x] Responses are gzip-compressed (faster over Tailscale and mobile).
23. [x] API responses are never cached.
24. [x] The development-mode file watcher is cached instead of rescanning on every poll.

### Server management
25. [x] Rename a server.
26. [x] Duplicate a server (checks disk space, picks a free port, leaves out secrets, turns RCON off).
27. [x] Memory per server, editable after creation.
28. [x] Optimized Java flags preset (Aikar's G1GC).
29. [x] Custom Java flags with strict validation.
30. [x] Player count on server cards.
31. [x] Playtime tracking with a top-25 leaderboard.
32. [x] Enable or disable a plugin or mod without deleting it.
33. [x] Delete a plugin or mod from the panel.
34. [x] Crash report list with one-click open in the editor.
35. [x] "History" button loads earlier lines of `logs/latest.log` into the console.
36. [x] Operators, whitelist and bans have inline DeOP, Remove and Pardon buttons.
37. [x] Server properties show a description for each known key and use the right input type.
38. [x] Filter box for server properties.

### Files
39. [x] Clickable breadcrumb path.
40. [x] Rename files and folders.
41. [x] Create a new file.
42. [x] Upload several files at once.
43. [x] File sizes and modified times in the list.
44. [x] Unsaved-changes marker, discard prompt, and a warning before leaving the page.
45. [x] Ctrl+S saves the open file.

### Interface
46. [x] Command palette (Ctrl+K): jump to pages, servers and common actions.
47. [x] Keyboard shortcuts (g then s/c/m/p/t, /, [, ?) with a help dialog.
48. [x] Collapsible sidebar, remembered between visits.
49. [x] Skip-to-content link.
50. [x] Server search.
51. [x] Server sort (newest, name, running first, port), remembered.
52. [x] Browser tab title shows how many servers are online; the tab icon changes colour.
53. [x] "Match my system" theme (light or dark).
54. [x] Reduced motion is the default when the operating system asks for it.
55. [x] Console text size controls.
56. [x] Copy the visible console lines.
57. [x] Quick-command chips (save world, who is online, day, clear weather, say).
58. [x] Copy server address.
59. [x] Relative times ("3 min ago") in file lists.
60. [x] Port, EULA and permission errors explain what to do next.

### Quality
61. [x] 37 more automated tests (88 in total), including the alert, shutdown and permission logic.
62. [x] CI workflow that runs the tests on Windows and Linux for every push.

## Shipped since v2.3.0

- [x] Modpack support in Plugins & Mods: browse Modrinth modpacks and create a Fabric server from one, with checksums, safe paths and a progress bar.
  Still to do for modpacks: Forge, NeoForge and Quilt packs (they need their installers run), CurseForge packs, and updating an installed pack in place.

## Backlog

### Security
63. [ ] QR code for two-factor setup instead of typing the key.
64. [ ] WebAuthn passkeys / hardware keys as a second factor.
65. [ ] Encrypt the TOTP secrets in `staff.json` with a key kept outside the data folder.
66. [ ] Session list with device names, last IP and per-session sign-out.
67. [ ] Idle timeout that signs you out after a configurable time.
68. [ ] Optional IP allow-list for the panel (for example only Tailscale addresses).
69. [ ] Email or Discord alert on sign-in from a new IP.
70. [ ] CSRF double-submit token as defence in depth.
71. [ ] Remove `'unsafe-inline'` from the CSP by moving inline handlers into the JS files.
72. [ ] Per-server permissions (staff may only touch the servers you choose).
73. [ ] Read-only "viewer" role that cannot change anything.
74. [ ] Time-limited staff access that expires by itself.
75. [ ] Rate limiting on every write endpoint, not just sign-in.
76. [ ] Signed releases and a checksum check before the updater installs files.
77. [ ] Scan uploaded plugin jars against Modrinth's hash database before installing.
78. [ ] Warn when a plugin has known vulnerabilities.

### Servers and gameplay
79. [ ] Server templates (save a server as a template, create new ones from it).
80. [ ] Change a server's Minecraft version in place, with an automatic backup first.
81. [ ] Move a server's world to another drive.
82. [ ] Import from a zip file as well as a folder, and from a local `.mrpack` file.
83. [ ] Pre-generate the world (chunk pre-generation) with a progress bar.
84. [ ] World border, gamerule and seed editor with explanations.
85. [ ] Datapack manager.
86. [ ] Resource-pack hosting and setting `resource-pack` automatically.
87. [ ] Per-server `server-icon.png` generated from the logo.
88. [ ] Bedrock/Geyser setup helper.
89. [ ] Proxy support (Velocity) with a network view of linked servers.
90. [ ] Whitelist request form that players fill in and staff approve.
91. [ ] Player profile page: skin, first seen, playtime, last position, ban history.
92. [ ] Look up UUIDs and names against Mojang when adding to the whitelist.
93. [ ] Scheduled commands (cron-like) per server.
94. [ ] Event hooks: run a command when a player joins or a server starts.
95. [ ] Maintenance mode: whitelist on, MOTD changed, players told, one switch.
96. [ ] Auto-stop an empty server after N minutes and start it again when someone connects.
97. [ ] Plugin configuration diff view between two backups.
98. [ ] Mod/plugin dependency checker.
99. [ ] One-click "Aikar flags" suggestion based on the server's RAM and player count.
100. [ ] Warn when the allocated memory is more than the PC can give.

### Backups
101. [ ] Upload backups to Google Drive, Backblaze B2 or an S3 bucket.
102. [ ] Verify a backup by test-extracting it on a schedule.
103. [ ] Backup retention by age and by count together.
104. [ ] Incremental backups (only changed files).
105. [ ] Back up automatically before a scheduled restart, an update or a version change.
106. [ ] Download a single file or folder out of a backup without restoring it.
107. [ ] Restore only the world, or only the plugins.
108. [ ] Show what changed between two backups.
109. [ ] Backup size trend chart.
110. [ ] Estimated time and size before a backup starts.

### Monitoring and notifications
111. [ ] TPS and tick-time graph (from Spark or the `tps` command).
112. [ ] Player-count history chart.
113. [ ] Longer history for CPU and memory (stored, not just the last minute).
114. [ ] Network traffic per server.
115. [ ] Alert thresholds you can change per server.
116. [ ] Quiet hours for notifications.
117. [ ] Email and Telegram channels besides Discord.
118. [ ] A Discord slash-command bot to start, stop and check servers from chat.
119. [ ] Daily summary message (uptime, peak players, backups, crashes).
120. [ ] Public read-only status page with a shareable link.
121. [ ] Prometheus `/metrics` endpoint.
122. [ ] Log search across all servers with date ranges.
123. [ ] Parse crash reports and name the plugin most likely at fault.
124. [ ] Highlight and link player names, coordinates and command output in the console.
125. [ ] Detect and explain common startup errors (port in use, EULA, wrong Java, out of memory).

### Interface
126. [ ] Drag and drop uploads onto the file manager.
127. [ ] Syntax highlighting and line numbers in the file editor.
128. [ ] Validate YAML and JSON before saving.
129. [ ] Diff view and "revert to last saved" in the editor.
130. [ ] Right-click context menus in the file list.
131. [ ] Bulk actions: select several files, servers or plugins.
132. [ ] Server groups and favourites on the list.
133. [ ] Customisable dashboard widgets.
134. [ ] Light/dark theme scheduling by time of day.
135. [ ] Translations (English plus a language file format others can add to).
136. [ ] Onboarding tour for the first run.
137. [ ] Undo for destructive actions where possible (delete to a recycle folder).
138. [ ] Installable app (PWA manifest and offline shell).
139. [ ] Mobile layout polish: bottom navigation and bigger touch targets.
140. [ ] Print-friendly diagnostics and activity pages.

### Platform and code
141. [ ] Run as a Windows service or tray app so it starts with the PC.
142. [ ] macOS and Linux launchers and a systemd unit.
143. [ ] Docker image.
144. [ ] Switch the background threads to a small job queue with visible status.
145. [ ] Move per-server settings from one JSON file to a small SQLite database.
146. [ ] OpenAPI description of the HTTP API.
147. [ ] Browser-level tests for the UI (Playwright) in CI.
148. [ ] Type-check the Python with mypy and lint with ruff in CI.
149. [ ] Plugin system so others can add tabs and automations.
150. [ ] Telemetry-free crash reporter that saves a redacted bug-report bundle on request.
