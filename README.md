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

## Importing a server folder

Choose the folder in the Create tab. The browser uploads it in batches of about 24 MB, so folder size is not limited by the request size; a single file must stay below 240 MB. If an upload fails partway, the staged files are discarded and nothing is added to `servers/`.

## Playit.gg

Install the Playit agent separately from [playit.gg](https://playit.gg/). Open a server in the panel, enter the agent command or executable path and your agent secret, then start the tunnel.

The secret is saved in that server's `manager_meta.json`. Keep the `servers/` directory private and do not commit it to a public repository.

## Development reload

`start.bat` enables development mode. Flask reloads Python and template changes, and the browser checks for changes to the app files and reloads the page when they change.

To run without development reload behavior:

```powershell
$env:MC_MANAGER_DEV = "0"
python app.py
```

## Project layout

```text
app.py                 Flask API and server process management
requirements.txt       Python dependencies
templates/index.html    Application markup
static/css/style.css   Application styles and themes
static/js/app.js       Browser behavior
static/js/map.js       Live map tab
static/js/scene.js     WebGL backdrop and server core
static/vendor/         Bundled Leaflet and three.js (no CDN, works offline)
mock_rcon.py           Fake RCON server with simulated players for development
servers/               Local server files and metadata
backups/               Server backup archives
.imports/              Staging area used while a folder import is uploading
setup.bat              Windows environment setup
start.bat              Windows development launcher
```

## Notes

- The first server start can take a while while Minecraft generates the world.
- A server must be running for console commands, player actions, and plugin reload commands.
- The default Minecraft port is `25565`; each server should use a different port.
- Modrinth, PaperMC, Purpur, and Mojang version APIs require an internet connection.
- Restoring a backup replaces every file in the server folder, including `manager_meta.json`.
- The live map needs RCON enabled on the server; without it the map shows no players.
- Setting `MC_MANAGER_TOKEN` requires an `X-Admin-Token` header on the map, marker, and RCON routes. The rest of the API is unauthenticated, so treat this as a small extra lock rather than real protection.
- Do not expose the panel directly to the public internet without adding authentication and access controls.
