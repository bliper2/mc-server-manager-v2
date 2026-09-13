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
- Import an existing server folder
- Start, stop, restart, and delete servers
- Live console output, command input, pause/resume updates, and log download
- Live Minecraft latency for each server
- Players, operators, whitelist, bans, and admin actions
- Vulcan command shortcuts and an anti-cheat settings panel
- Properties form editor and raw `server.properties` editor
- Plugin and mod search through Modrinth
- Top downloaded plugin and mod lists
- Plugin YAML, YML, and JSON config editor with download support
- Playit.gg agent configuration and start/stop controls
- Server logo generation and custom logo import
- Themes, fonts, density, refresh, and confirmation settings
- Commands Wiki with searchable Minecraft command examples

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
servers/               Local server files and metadata
setup.bat              Windows environment setup
start.bat              Windows development launcher
```

## Notes

- The first server start can take a while while Minecraft generates the world.
- A server must be running for console commands, player actions, and plugin reload commands.
- The default Minecraft port is `25565`; each server should use a different port.
- Modrinth, PaperMC, Purpur, and Mojang version APIs require an internet connection.
- Do not expose the panel directly to the public internet without adding authentication and access controls.
