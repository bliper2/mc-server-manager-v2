# Minecraft Server Manager

A small local web panel for creating and managing Minecraft Java servers. It uses Flask on the backend and plain HTML, CSS, and JavaScript in the browser.

The project currently supports Paper, Purpur, and Vanilla servers. Server data is kept in the local `servers/` directory.

## Requirements

- Python 3.10 or newer
- Java installed and available on `PATH` (Java 17, 21, or the version required by your Minecraft server)

## Windows setup

Double-click `start.bat`. It creates `.venv` when needed, installs the requirements, and starts the panel.

For setup only, run `setup.bat`.

## Manual setup

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000/` after the server starts.

## What is included

- Create Paper, Purpur, and Vanilla servers
- Choose the Minecraft version, RAM, port, player limit, game mode, difficulty, MOTD, PvP, whitelist, and command-block settings
- RAM presets and an exact MB field covering 512 MB to 64 GB, with the installed and free memory of the host shown alongside
- Import an existing server folder of any size, uploaded in batches with progress
- Backups: create, label, download, restore, scheduled automatic runs, and pruning to the last N copies
- Start, stop, restart, and delete servers
- Live console output, command input, pause/resume updates, and log download
- Live Minecraft latency for each server
- Players, operators, whitelist, bans, and admin actions
- Vulcan command shortcuts and an anti-cheat settings panel
- Properties form editor and raw `server.properties` editor
- Plugin and mod search through Modrinth, plus update checking and one-click updates for installed jars
- Top downloaded plugin and mod lists
- Plugin YAML, YML, and JSON config editor with download support
- Playit.gg agent configuration and start/stop controls
- Server logos and banners: 141 bundled logos (13 animated GIFs) and 18 bundled banners (8 animated GIFs), a community library on GitHub, bulk image import and Pinterest pin import
- 36 accent colours, each tuned for the dark and the light themes
- Live map of online players with a moderation drawer, over RCON
- Self-update from the GitHub repo, with a snapshot and rollback
- 37 themes, seven fonts, density, refresh, and confirmation settings
- Optional animated 3D backdrop and a server core that reacts to player count
- Commands Wiki with searchable Minecraft command examples

## Live map

The **Live Map** tab plots online players on a 2D grid built from their real coordinates, alongside
structure markers you place yourself.

Positions come from the server's RCON port, so it has to be switched on once per server. Open the tab,
fill in the RCON panel (leave the password blank to have one generated), save, and restart the server —
the manager writes `enable-rcon`, `rcon.port`, `rcon.password` and `broadcast-rcon-to-ops=false` into
that server's `server.properties`. The panel hides itself once RCON is live.

Clicking a player opens a moderation drawer: OP/de-OP, whitelist, kick, ban with a reason, unban,
teleport to coordinates or to another player, heal, kill, freeze and give. Every command is sent over
RCON when it is available, so the action log shows the server's own reply rather than a guess. Mute and
inventory inspection are not in vanilla and need a permissions plugin; the drawer says so rather than
offering buttons that do nothing.

Structure markers are yours to place — no server API can enumerate player builds. Name one, pick a type,
press **Place on map** and click the spot. They are stored per server in `map_markers.json`.

### Developing without a Minecraft server

`mock_rcon.py` is a fake RCON server with five simulated players walking in circles, so the map and the
moderation drawer can be worked on with nothing else running:

```powershell
python mock_rcon.py --port 25575 --password devpass
```

Point a server's RCON settings at that port and password. Commands you issue are printed to its console
instead of being executed.

## Backups

Open a server and use the **Backups** tab. A backup is a ZIP of that server folder; `logs`, `crash-reports`, `cache`, `debug`, `libraries`, `versions`, `session.lock`, and `usercache.json` are skipped.

- World folders are included by default and can be left out for a quick config-only copy.
- If the server is running, saving is flushed and paused (`save-off`, `save-all flush`) while the archive is written, then resumed (`save-on`).
- **Keep last** prunes older backups after each new one. Set it to `0` to keep every backup.
- Restoring requires the server to be stopped. The current files are archived automatically first, then replaced with the contents of the backup.

Backups live in `backups/<server id>/` next to `app.py` and are removed when the server is deleted.

### Automatic backups

The **Automatic backups** card on the same tab runs them on a schedule without you being there. Turn it
on, pick an interval between one hour and seven days, and choose whether world folders are included; the
**Keep last** count above applies to these too. A background check runs every five minutes and starts a
backup when one is due, skipping a server that already has a backup or restore running. Automatic copies
are tagged as such in the list, and the card shows when the last one ran.

## Plugin and mod updates

The **Plugin & mod updates** card in a server's Plugins/Mods tab hashes every installed jar and looks it
up on Modrinth by file hash, so it identifies what a jar actually is rather than guessing from the
filename. Anything it recognises is compared against the newest release for that server's Minecraft
version.

Update one jar or all of them at once, or set a schedule (6 hours to 3 days) and optionally let it
install updates on its own. Jars that are not on Modrinth are listed as unmatched and left alone.

Updating replaces a file on disk, so it works best with the server stopped — Windows will not let a
running server's jar be overwritten, and the manager reports that rather than failing quietly.

### The right loader

Modrinth lists one project under several loaders (Paper, Fabric, NeoForge, Folia and more), so "the newest version" can be a build your server cannot run. The manager now asks only for builds the server's type accepts: Paper takes Paper, Spigot and Bukkit plugins, Purpur those plus Purpur, and Fabric, Forge and NeoForge servers take their own mods. This applies to **Install**, to the update check and to **Apply update**, and a downloaded file is also looked inside before it is saved: a NeoForge or Fabric mod headed for a Paper `plugins` folder, a Folia-only build or a cut-off download is refused with the reason.

Files that are already in the wrong place are shown in the Plugins/Mods tab with a warning and the reason (for example "This is a NeoForge mod, not a plugin, so Purpur cannot load it" or "Needs ProtocolLib, which is not installed"). **Disable them all** renames them to `.jar.disabled`, so Paper stops logging a stack trace for each one at start-up, and you can turn them back on at any time.

## Updating the manager

**Settings > Updates** compares the installed version with GitHub. It follows one of two channels:

- **Releases** (default, recommended): the newest published GitHub release.
- **Latest code**: the newest commit on `main`.

**Check now** lists the new commits, the release notes and every file that would change. **Install update**
downloads that version as a zip and writes it over the manager's own files, then **restarts the manager by
itself and reloads this page**; there is no need to close the terminal or reopen the site. Servers that were
running are stopped properly (the world is saved) and started again once the manager is back. You are asked
before that happens.

- It refuses an archive that does not contain `app.py`, `templates/index.html` and `manager/__init__.py`.
- It never writes to `servers/`, `backups/`, `.imports/`, `.venv/`, `.git/`, `staff.json`, `.secret_key`,
  `audit.jsonl`, `manager_settings.json` or `update_state.json`.
- The files it replaces are zipped into `backups/_manager/` first; **Roll back last update** restores the
  newest of those (the last five are kept) and restarts too.
- **Install automatically** is off by default and only ever installs published releases, never a raw commit.
  After an automatic install the manager restarts itself the next time no server is running.
- Updating needs the owner account.

The self-restart works when the manager was started with `start.bat`, `start-shared.bat` or `python app.py`.
Every open browser tab notices the restart (or, in development mode, a changed source file) and reloads itself.

Point `MC_MANAGER_REPO` at another `owner/name` to follow a fork. A copy cloned with git knows its version from
`git rev-parse HEAD` until the first update.

## Everyday tools

- **Ctrl+K** opens a palette to jump to any page or server. **?** lists every keyboard shortcut (`g` then `s`/`c`/`m`/`p`/`t` to switch pages, `/` to search servers, `[` to collapse the sidebar, Ctrl+S to save a file).
- **Server list:** search, sort (newest, name, running first, port), live player counts, CPU and memory.
- **Server page:** rename or duplicate a server (the copy gets its own port, no Playit secret and RCON off), copy its address, and see who has been online the longest under **Players**.
- **Launch settings** (Properties tab): memory per server and a Java flags preset (*Default*, *Optimized* using Aikar's G1GC flags, or *Custom*). Custom flags are limited to plain `-X`, `-XX` and `-D` options.
- **Console:** filter by text or level, jump to latest, `Tab` to complete commands, arrow keys for history, quick-command chips, text size, copy, and **History** to load earlier lines from `logs/latest.log`.
- **Files:** breadcrumb path, rename, new file, multi-file upload, an unsaved-changes marker, and crash reports under the **Activity** tab.
- **Plugins and mods:** switch one off without deleting it (it becomes `.jar.disabled`) or delete it.
- **Properties:** every known key has a description and the right kind of input, with a filter box.
- **EULA:** creating a server needs a tick on "I accept the Minecraft EULA".

## Modpacks (Modrinth and CurseForge)

**Plugins & Mods** can create a server from a modpack. The search box has *Modpacks (Modrinth)* and *Modpacks (CurseForge)*, and the page lists popular packs from both. **Create server** asks for a pack version, a name, a port and memory (modpacks need 4 GB or more), then installs in the background with a progress bar that survives closing the dialog or reloading the page.

**Supported mod loaders: Fabric, Forge and NeoForge.** Quilt packs are refused with a message naming the loader. Forge and NeoForge are installed by running their official installer once (it needs the right Java; Settings > Java can install it). Minecraft 1.17 and newer Forge/NeoForge servers start from the installer's arguments file, older Forge from its jar. NeoForge for Minecraft 1.20.1 uses an older layout the manager does not handle.

**Modrinth packs (`.mrpack`)** need no account. The manager downloads the pack, installs the loader, downloads every server-side mod (four at a time, skipping client-only ones) and applies the pack's `overrides/` and `server-overrides/`.

**CurseForge packs** need your own free API key from [console.curseforge.com](https://console.curseforge.com/), saved under *Settings > CurseForge* (owner only; stored in `manager_settings.json`, never shown again). When a pack version has an official **server pack**, that is used. Otherwise the server is built from the client pack's mod list, and the manager warns that client-only mods may need removing. Some mod authors turn off third-party downloads; the manager does not work around that. It names those mods before creating anything, so you can pick a version with a server pack or download those files yourself. Resource packs and shaders in a pack's list are skipped, since a server cannot use them.

Some CurseForge keys are not allowed to use CurseForge's search endpoint (everything else works). The manager detects this: the CurseForge list then shows featured, popular and recently updated packs, and a note says so. To use any other pack, enter its **Project ID** (shown on its CurseForge page under *About*) in the box above the list, or type the number into the search box.

Safety for both: HTTPS only; downloads only from the hosts each format allows (Modrinth: `cdn.modrinth.com`, `github.com`, `raw.githubusercontent.com`, `gitlab.com`; CurseForge: `edge.forgecdn.net`, `mediafilez.forgecdn.net`, `media.forgecdn.net`; loaders: `maven.minecraftforge.net`, `maven.neoforged.net`, `meta.fabricmc.net`); every file is checked against the checksum the pack or API gives, and one mismatch cancels the install and removes the half-built server; size and file-count limits; paths that escape the server folder or overwrite the manager's own files are refused; one pack installs at a time; and it needs the *create, import and delete servers* permission. The new server shows its pack and source under **Info**.

Not supported yet: Quilt, installing a pack into an existing server, importing a local pack file, and updating an installed pack in place.

## Server logos and banners

**Create** and each server's **Info** panel have a logo library. It holds 141 ready-made logos (emblems in 13 colour schemes, pixel-art icons and 13 animated GIFs; search by name or filter by Animated, Combat, Royal, Nature, Build and Pixel art), the **Community** logos from GitHub and everything you add yourself under **My logos**:

- **The `logos` folder.** Drop PNG, JPG, GIF or WebP files into the `logos` folder in the main manager folder (next to `app.py`), with any file name (even none: saved web pages often have no extension), then open the picker (or press **Refresh**). They appear under **My logos**. The manager creates the folder on start. Files are checked by content, up to 8 MB each; anything that is not really an image is ignored. **GIFs stay animated.**
- **Upload images** in the dialog does the same thing: it takes several files at once and saves them into that folder.
- **Import pin** takes the link of a Pinterest pin (or a `pin.it` short link) and stores the pin's image in your library. Pinterest has no public search for other programs, so the **Browse Pinterest** button opens Pinterest's own search in a new tab: find a pin, copy its link, paste it here. Only `pinterest.com` pin pages and `i.pinimg.com` images are ever fetched, every redirect is checked, and the size is capped. Use only images you have the right to use.

Importing or removing library logos needs the *create, import and delete servers* permission; the ✕ on a My logos tile deletes that file from the `logos` folder. **Banners** come as 18 ready-made pictures (10 stills and 8 looping animated GIFs: aurora, starfield, waves, neon grid, embers, rain, lava and sunset clouds, filterable as Animated, Scenic and Patterns), the Community banners and your own. They are wide pictures (or animated GIFs, up to 16 MB) shown at the top of a server's Info panel and across the top of its card in the server list. Use **Banner** in the Info panel: it opens the same picker for the `banners` folder, with the same drop-a-file, upload and Pinterest options, and **Remove** takes the banner off again. Banners need the *files* permission to set, like logos.

**Sharing through GitHub.** Anything in `static/community/logos` and `static/community/banners` in the repository ships with the manager and shows up under **Community** for everyone who updates. To contribute, put your file in the right folder (or run `python tools/add_community.py logo|banner FILE`) and open a pull request; the rules (only art you made or that is openly licensed, no brand or game logos, no Pinterest saves) are in [static/community/README.md](static/community/README.md). A test fails if a contributed file is not a usable image. The `logos` and `banners` folders next to `app.py` stay private to your install and are never committed.

A server keeps its own copy of the logo or banner it uses (`manager_logo.*` in its folder), so removing a library logo never changes a server, and backups and clones include it. The bundled logos and banners are original artwork drawn by `tools/make_logos.py` and `tools/make_banners.py` (they need Pillow; only needed to change the library), so they carry no licence.

## Health, diagnostics and alerts

- `GET /api/health` is public and returns only `{ok, version, uptime}`, for uptime monitors.
- **Settings > Diagnostics** (owner) lists versions, Java, disk space, uptime and update state, with a copy button for bug reports. **Manager log** shows `manager.log` (rotated at 1 MB, three kept).
- Quiet watchers tell Discord when a server stays above 90% CPU or 92% of its memory for two minutes, when a running server stops answering for five minutes, and when the drive has under 5 GB free.
- Closing the terminal or pressing Ctrl+C in normal mode saves and stops running servers instead of leaving them orphaned (development mode leaves them running so reloads do not interrupt them).
- Responses are gzip-compressed, static files are cached for a year under a versioned URL, and every page carries a Content-Security-Policy and the usual security headers. The session cookie is marked Secure when the page is reached over HTTPS.

## Notifications (Discord)

Settings > Discord notifications (owner only). Paste a channel webhook (Channel settings > Integrations >
Webhooks) and choose what to hear about: server start, stop, crash and automatic restart, backups, manager
updates, lockouts and failed sign-ins, staff changes, resource, hang and disk alerts, and optionally players joining or leaving. The URL is stored
in `manager_settings.json`, is never sent back to the browser, and is not overwritten by updates.

### Discord status board (per server)

Every server's page has a **Discord status** card. Paste a Discord webhook URL (channel settings > Integrations > Webhooks > New webhook > Copy URL) and the manager posts one message in that channel that says **Online**, **Starting up** or **Offline**, with the **address** people join on, the player count, the version and when it last changed. The message is edited in place, never re-posted, so the channel stays tidy. It updates within a few seconds of the server starting, stopping or crashing, and the player count refreshes every 5, 10, 30 or 60 minutes (or only on changes, your choice).

The address shown is, in this order: what you type in **Address to show**, the Playit tunnel address when the tunnel is running, this PC's public IP with the server port, or `localhost`. The card says which one it will use. **Post now** sends the message immediately and shows Discord's answer if it fails; **Remove** deletes the message from the channel and forgets the webhook.

The webhook URL is stored in `manager_settings.json` (git-ignored), is never sent back to the browser (only its last six characters), and needs the *create, import and delete servers* permission to change. If the manager itself is switched off the board cannot update, but it says Offline when the manager shuts down normally.

## Server automation

Open a server and choose **Automation**:

- **Crash recovery:** restarts the server if its process dies. Stopping it from the panel or in game is not a
  crash. After the configured number of crashes within the window it gives up and says so.
- **Scheduled restart:** a daily restart at a time you choose, with chat warnings first and a world save.
- **Announcements:** rotating chat messages while players are online.

The **Activity** tab shows who did what on that server.

## Importing a server folder

Choose the folder in the Create tab. The browser uploads it in batches of about 24 MB, so folder size is not limited by the request size; a single file must stay below 240 MB. If an upload fails partway, the staged files are discarded and nothing is added to `servers/`.

## Playit.gg

Install the Playit agent separately from [playit.gg](https://playit.gg/). Open a server in the panel, enter the agent command or executable path and your agent secret, then start the tunnel.

The secret is saved in that server's `manager_meta.json`. Keep the `servers/` directory private and do not commit it to a public repository.

## Development reload

`start.bat` enables development mode. Flask reloads Python and template changes, and open pages reload
themselves when the app files change. It also turns on Flask's debug mode, so use `start-shared.bat` (debug
off) whenever other people can reach the panel.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

GitHub Actions runs them on Windows and Linux for every push. The tests run against a temporary data folder (`MC_MANAGER_HOME`), so they never touch your servers or accounts.
They cover sign-in, lockout, two-factor, permissions (including a check that every state-changing route has been
classified), path and input safety, console offsets, restore safety, Java rules, crash recovery, scheduling,
notifications, updates and the restart flow.

## Project layout

```text
app.py                     Entry point and the restart supervisor
manager/                   Application code (Flask routes register themselves on manager.app)
  config.py  state.py        Paths, constants, in-memory runtime state
  store.py  util.py          Metadata, settings and audit trail; safe-path and validation helpers
  auth.py  totp.py           Accounts, sessions, permissions, two-factor
  procs.py  javatools.py     Minecraft and Playit processes; finding and installing Java
  notify.py  metrics.py      Discord webhook; CPU and memory numbers
  backups.py  providers.py   Backups; jar downloads and the Modrinth client
  rconmap.py                 RCON client, live map, markers
  updater.py  lifecycle.py   Self-update; restarting the manager
  routes_servers.py  routes_files.py  servertools.py  automation.py   HTTP routes and background jobs
  modpacks.py  curseforge.py  loaders.py  packtools.py   Modpack install: Modrinth and CurseForge sources, Fabric/Forge/NeoForge loaders, shared safe downloads
  logos.py  compat.py        Logo library and image import; which plugin/mod files a server can load
  ops.py  web.py             Health, diagnostics, log, disk; security headers, gzip, caching
tests/                     Unit tests (python -m unittest discover -s tests -t .)
templates/                 index.html (the panel) and login.html
static/css/themes.css      One colour block per theme
static/css/app.css         All other styles
static/js/                 core, servers, detail, console, files, backups, plugins, settings, staff,
                           automation, modpacks, logos, palette, boot, main (plus map.js and scene.js)
static/logos/              The bundled server logos, 141 files (made by tools/make_logos.py)
static/banners/            The 18 bundled banners (made by tools/make_banners.py)
static/community/          Logos and banners contributed through GitHub (see its README)
logos/                     Your own logo images (git-ignored; created on start)
banners/                   Your own banner images and GIFs (git-ignored; created on start)
static/vendor/             Bundled Leaflet and three.js (no CDN, works offline)
mock_rcon.py               Fake RCON server with simulated players for development
servers/  backups/         Your data (git-ignored)
staff.json  .secret_key  audit.jsonl  manager_settings.json  manager.log  update_state.json   Local state (git-ignored)
ROADMAP.md                 What shipped and the 88 ideas still on the list
setup.bat  start.bat  start-shared.bat   Windows setup and launchers
```

`MC_MANAGER_HOME` moves everything the manager writes to another folder, and `MC_MANAGER_PORT` changes the port.

## Accounts and staff panel

The panel needs a sign-in. There is one **owner** and room for **two staff**, so at most three people can ever get in.

- **First run:** open `http://127.0.0.1:5000` on the PC that runs the manager and create the owner account. Setup is refused from any other address, including through Tailscale, so nobody else can claim the panel first.
- **Staff panel** (owner only): add, reset or remove the two staff accounts, choose what each may do, reset their two-factor, and read the activity log. Removing an account or resetting its password signs that person out immediately.
- **Permissions:** everyone can look at everything. Staff can only change what the owner allows: *start, stop and restart*, *console and moderation*, *files, properties and plugins*, *backups*, and *create, import and delete servers*. Only the owner manages accounts, updates, Discord and Java installs.
- **Passwords** are never stored. Only salted scrypt hashes go into `staff.json`. Passwords must be 8+ characters and not an obvious one (`password123`, `minecraft`...); five wrong attempts lock that username and address for five minutes.
- **Two-factor sign-in** (Settings > Two-factor sign-in): works with any authenticator app. It asks for a 6-digit code after the password; each code works once, and eight one-time recovery codes are shown when you turn it on. The TOTP secret is stored in `staff.json`, so keep that file private. Turning it off needs your password and a code.
- **Sign out everywhere else** (Settings > Your account) ends every other session of your account. The Staff panel shows who is active right now and exports the activity log as CSV.
- **Forgot the owner password:** stop the manager, delete `staff.json`, start it again, and create the owner from the host PC. Servers and backups are untouched.
- `manager_meta.json` (it holds the Playit secret) can only be opened by the owner; RCON passwords never leave the server.

## Notes

- The first server start can take a while while Minecraft generates the world.
- A server must be running for console commands, player actions, and plugin reload commands.
- The default Minecraft port is `25565`; each server should use a different port.
- Modrinth, PaperMC, Purpur, and Mojang version APIs require an internet connection.
- Restoring a backup replaces every file in the server folder, including `manager_meta.json`.
- The live map needs RCON enabled on the server; without it the map shows no players.
- Setting `MC_MANAGER_TOKEN` additionally requires an `X-Admin-Token` header on the map, marker, RCON and manager-update routes. Normal use does not need it now that sign-in exists.
- The manager listens on `127.0.0.1` only. To reach it from another PC use `tailscale serve --bg 5000`; do not use `tailscale funnel` or port-forward it to the public internet.
- `start.bat` runs with `MC_MANAGER_DEV=1`, which turns on Flask's debug mode. Use `start-shared.bat` (debug off) before other people reach the panel.
- Java is looked up on `PATH`, then `JAVA_HOME`, then the usual Windows install folders (Adoptium, Microsoft, Zulu, Corretto, Oracle). The manager picks the lowest installed Java that the server's Minecraft version needs, refuses to start a server on one that is too old, and Settings > Java can install Java 21 with winget.
- Creating a server on a port another server already uses is refused.
