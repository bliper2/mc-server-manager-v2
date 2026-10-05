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
- Server logo generation and custom logo import
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

## Notifications (Discord)

Settings > Discord notifications (owner only). Paste a channel webhook (Channel settings > Integrations >
Webhooks) and choose what to hear about: server start, stop, crash and automatic restart, backups, manager
updates, lockouts and failed sign-ins, staff changes, and optionally players joining or leaving. The URL is stored
in `manager_settings.json`, is never sent back to the browser, and is not overwritten by updates.

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

The tests run against a temporary data folder (`MC_MANAGER_HOME`), so they never touch your servers or accounts.
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
  routes_servers.py  routes_files.py  automation.py   HTTP routes and background jobs
tests/                     Unit tests (python -m unittest discover -s tests -t .)
templates/                 index.html (the panel) and login.html
static/css/themes.css      One colour block per theme
static/css/app.css         All other styles
static/js/                 core, servers, detail, console, files, backups, plugins, settings, staff,
                           automation, boot, main (plus map.js and scene.js)
static/vendor/             Bundled Leaflet and three.js (no CDN, works offline)
mock_rcon.py               Fake RCON server with simulated players for development
servers/  backups/         Your data (git-ignored)
staff.json  .secret_key  audit.jsonl  manager_settings.json  update_state.json   Local state (git-ignored)
setup.bat  start.bat  start-shared.bat   Windows setup and launchers
```

`MC_MANAGER_HOME` moves everything the manager writes to another folder, and `MC_MANAGER_PORT` changes the port.

## Accounts and staff panel

The panel needs a sign-in. There is one **owner** and room for **two staff**, so at most three people can ever get in.

- **First run:** open `http://127.0.0.1:5000` on the PC that runs the manager and create the owner account. Setup is refused from any other address, including through Tailscale, so nobody else can claim the panel first.
- **Staff panel** (owner only): add, reset or remove the two staff accounts, choose what each may do, reset their two-factor, and read the activity log. Removing an account or resetting its password signs that person out immediately.
- **Permissions:** everyone can look at everything. Staff can only change what the owner allows: *start, stop and restart*, *console and moderation*, *files, properties and plugins*, *backups*, and *create, import and delete servers*. Only the owner manages accounts, updates, Discord and Java installs.
- **Passwords** are never stored. Only salted scrypt hashes go into `staff.json`. Passwords must be 8+ characters; five wrong attempts lock that username and address for five minutes.
- **Two-factor sign-in** (Settings > Two-factor sign-in): works with any authenticator app. It asks for a 6-digit code after the password; each code works once, and eight one-time recovery codes are shown when you turn it on. The TOTP secret is stored in `staff.json`, so keep that file private. Turning it off needs your password and a code.
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
