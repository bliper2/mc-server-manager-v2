"""Harmless pranks for a server's own players: fake chat lines, jump scares, sounds, short effects and silly spawns.

Every prank is a timeline of console commands. Nothing here damages a player, a world or an inventory:
effects are short, falling is cushioned, spawned animals are invulnerable, and "Calm them down" undoes the rest."""

import json
import struct
import threading

from flask import jsonify, request

from . import app
from .auth import current_account
from .procs import send_command
from .rconmap import Rcon, RconError, rcon_settings
from .store import audit, is_running, load_meta
from .util import PLAYER_NAME_PATTERN, has_line_break

MAX_TIMELINE_SECONDS = 30
MAX_PENDING_PER_SERVER = 6
MAX_TARGETS = 20
TEXT_LIMIT = 80

_pending: dict = {}
_pending_lock = threading.Lock()

TITLE_COLORS = ("white", "red", "gold", "yellow", "green", "aqua", "light_purple", "dark_red")
DEATH_CAUSES = {
    "slain": "was slain by Herobrine",
    "void": "fell out of the world",
    "creeper": "was blown up by Creeper",
    "lava": "tried to swim in lava",
    "floor": "discovered the floor was lava",
    "magic": "was killed by magic",
    "fire": "went up in flames",
    "high": "fell from a high place",
}
EFFECTS = {
    "blindness": "Blindness", "darkness": "Darkness", "nausea": "Nausea", "slowness": "Slowness",
    "glowing": "Glowing", "jump_boost": "Jump boost", "speed": "Speed", "levitation": "Levitation",
}


def j(value) -> str:
    """JSON text for tellraw and title. json.dumps does the escaping, so a quote in a name cannot break out."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text(params: dict, key: str, default: str) -> str:
    value = str(params.get(key) if params.get(key) not in (None, "") else default).strip()
    return value[:TEXT_LIMIT] or default


def number(params: dict, key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def grey(message: str) -> str:
    return j({"text": message, "color": "gray", "italic": True})


# Each builder returns [(seconds_from_start, command)]. `p` is a player name, or "@a" for global pranks.
def b_fake_op(p, a):
    return [(0, f"tellraw {p} {grey(f'[Server: Made {p} a server operator]')}")]

def b_fake_deop(p, a):
    return [(0, f"tellraw {p} {grey(f'[Server: Made {p} no longer a server operator]')}")]

def b_fake_whisper(p, a):
    sender, message = text(a, "from", "Herobrine"), text(a, "message", "I can see you.")
    return [(0, f"tellraw {p} {grey(sender + ' whispers to you: ' + message)}")]

def b_fake_chat(p, a):
    sender, message = text(a, "from", "Herobrine"), text(a, "message", "hello")
    return [(0, f"tellraw @a {j('<' + sender + '> ' + message)}")]

def b_fake_join(p, a):
    return [(0, f"tellraw @a {j({'text': text(a, 'name', 'Herobrine') + ' joined the game', 'color': 'yellow'})}")]

def b_fake_leave(p, a):
    return [(0, f"tellraw @a {j({'text': text(a, 'name', 'Herobrine') + ' left the game', 'color': 'yellow'})}")]

def b_fake_advancement(p, a):
    title = text(a, "title", "Stay Hydrated")
    return [(0, f"tellraw @a {j([{'text': f'{p} has made the advancement '}, {'text': f'[{title}]', 'color': 'green'}])}")]

def b_fake_death(p, a):
    cause = DEATH_CAUSES.get(str(a.get("cause")), DEATH_CAUSES["slain"])
    return [(0, f"tellraw @a {j(f'{p} {cause}')}")]

def b_title(p, a):
    color = a.get("color") if a.get("color") in TITLE_COLORS else "white"
    return [(0, f"title {p} times 10 60 20"),
            (0, f"title {p} subtitle {j({'text': text(a, 'subtitle', ''), 'color': 'gray'})}" if a.get("subtitle") else f"title {p} subtitle {j('')}"),
            (0, f"title {p} title {j({'text': text(a, 'title', 'Look behind you'), 'color': color})}")]

def b_you_died(p, a):
    return [(0, f"title {p} times 5 50 20"), (0, f"title {p} subtitle {j({'text': 'Score: 0', 'color': 'white'})}"),
            (0, f"title {p} title {j({'text': 'You Died!', 'color': 'red', 'bold': True})}"),
            (0, f"playsound minecraft:entity.player.death master {p}")]

def b_actionbar(p, a):
    return [(0, f"title {p} actionbar {j({'text': text(a, 'message', 'Someone is watching you.'), 'color': 'red'})}")]

def sound(name):
    return lambda p, a: [(0, f"execute at {p} run playsound minecraft:{name} master {p} ~ ~ ~ 1 1")]

def b_heartbeat(p, a):
    return [(i * 0.8, f"playsound minecraft:entity.warden.heartbeat master {p}") for i in range(6)]

def b_knock(p, a):
    return [(i * 0.55, f"playsound minecraft:entity.zombie.attack_wooden_door master {p}") for i in range(3)]

def b_footsteps(p, a):
    return [(i * 0.45, f"execute at {p} run playsound minecraft:block.stone.step master {p} ^ ^ ^{-5 + i * 0.6:.1f} 1 1") for i in range(7)]

def effect(name, seconds, level=1, after=None):
    def build(p, a):
        length = int(number(a, "seconds", seconds, 1, 30))
        steps = [(0, f"effect give {p} minecraft:{name} {length} {level} true")]
        if after:
            steps.append((0, f"effect give {p} minecraft:{after} {length + 8} 0 true"))
        return steps
    return build

def b_lag(p, a):
    return [(t, f"effect give {p} minecraft:slowness 1 255 true") for t in (0, 1.4, 2.6, 3.4)]

def b_spin(p, a):
    return [(i * 0.07, f"execute as {p} at @s run tp @s ~ ~ ~ ~20 ~") for i in range(18)]

def b_look_up(p, a):
    return [(0, f"execute as {p} at @s run tp @s ~ ~ ~ ~ -90")]

def b_look_down(p, a):
    return [(0, f"execute as {p} at @s run tp @s ~ ~ ~ ~ 90")]

def b_tp_to(p, a):
    target = str(a.get("target") or "").strip()
    if not PLAYER_NAME_PATTERN.fullmatch(target):
        raise ValueError("Type the name of the player to teleport to")
    return [(0, f"tp {p} {target}")]

def b_chickens(p, a):
    return [(i * 0.15, f"execute at {p} run summon minecraft:chicken ~{(i % 5) - 2} ~7 ~{(i % 3) - 1} {{Invulnerable:1b}}") for i in range(10)]

def b_bats(p, a):
    return [(i * 0.1, f"execute at {p} run summon minecraft:bat ~ ~2 ~ {{Invulnerable:1b}}") for i in range(12)]

def particle(name, count, spread, speed=0):
    return lambda p, a: [(0, f"execute at {p} run particle minecraft:{name} ~ ~1.5 ~ {spread} {spread} {spread} {speed} {count} force {p}")]

def b_totem(p, a):
    return [(0, f"execute at {p} run particle minecraft:totem_of_undying ~ ~1 ~ 0.5 0.8 0.5 0.4 60 force {p}"),
            (0, f"playsound minecraft:item.totem.use master {p}")]

def b_haunting(p, a):
    steps = [(0, f"effect give {p} minecraft:darkness 14 0 true"), (0, f"execute at {p} run playsound minecraft:ambient.cave master {p} ~ ~ ~ 1 1")]
    steps += [(2 + i * 0.8, f"playsound minecraft:entity.warden.heartbeat master {p}") for i in range(5)]
    steps += [(5 + i * 0.45, f"execute at {p} run playsound minecraft:block.stone.step master {p} ^ ^ ^{-5 + i * 0.8:.1f} 1 1") for i in range(5)]
    steps += [(8.5, f"execute at {p} run playsound minecraft:entity.creeper.primed master {p} ^ ^ ^-2 1 1"),
              (9.5, f"title {p} title {j({'text': 'Behind you.', 'color': 'dark_red'})}")]
    return steps

def b_herobrine(p, a):
    return [(0, f"tellraw @a {j({'text': 'Herobrine joined the game', 'color': 'yellow'})}"),
            (3, f"tellraw @a {j('<Herobrine> I see you.')}"),
            (3, f"effect give {p} minecraft:darkness 8 0 true"),
            (3.5, f"execute at {p} run playsound minecraft:ambient.cave master {p} ~ ~ ~ 1 1"),
            (7, f"tellraw @a {j({'text': 'Herobrine left the game', 'color': 'yellow'})}")]

def b_calm(p, a):
    return [(0, f"effect clear {p}"), (0, f"stopsound {p}"), (0, f"title {p} clear")]


def prank(id, name, category, description, build, params=(), scope="player", seconds=0):
    return {"id": id, "name": name, "category": category, "description": description, "build": build,
            "params": list(params), "scope": scope, "seconds": seconds}

def field(key, label, kind="text", default="", **extra):
    return {"key": key, "label": label, "type": kind, "default": default, **extra}

PRANKS = [
    prank("fake_op", "Fake OP", "Chat", "Shows the grey 'made you a server operator' line, as if they were just opped.", b_fake_op),
    prank("fake_deop", "Fake DeOP", "Chat", "Tells them they are no longer an operator.", b_fake_deop),
    prank("fake_whisper", "Fake whisper", "Chat", "A private message that looks like it came from someone else.", b_fake_whisper,
          [field("from", "From", default="Herobrine"), field("message", "Message", default="I can see you.")]),
    prank("fake_chat", "Fake chat line", "Chat", "Everyone sees a chat line from any name you like.", b_fake_chat,
          [field("from", "Name", default="Notch"), field("message", "Message", default="hello")], scope="global"),
    prank("fake_join", "Fake join", "Chat", "Everyone sees that someone joined.", b_fake_join, [field("name", "Name", default="Herobrine")], scope="global"),
    prank("fake_leave", "Fake leave", "Chat", "Everyone sees that someone left.", b_fake_leave, [field("name", "Name", default="Herobrine")], scope="global"),
    prank("fake_advancement", "Fake advancement", "Chat", "Broadcasts an advancement the player never earned.", b_fake_advancement,
          [field("title", "Advancement", default="Stay Hydrated")], scope="global"),
    prank("fake_death", "Fake death message", "Chat", "Broadcasts that the player died, while they are fine.", b_fake_death,
          [field("cause", "How", "select", "slain", options=[[k, v] for k, v in DEATH_CAUSES.items()])], scope="player"),
    prank("title", "Big title", "Screen", "Puts large text in the middle of their screen.", b_title,
          [field("title", "Title", default="Look behind you"), field("subtitle", "Subtitle"), field("color", "Colour", "select", "red", options=[[c, c.replace("_", " ")] for c in TITLE_COLORS])], seconds=4),
    prank("you_died", "Fake 'You Died!'", "Screen", "The red death title with the death sound. They are still alive.", b_you_died, seconds=4),
    prank("actionbar", "Action bar whisper", "Screen", "Small red text above the hotbar.", b_actionbar, [field("message", "Text", default="Someone is watching you.")], seconds=4),
    prank("creeper", "Creeper hiss", "Sound", "The fuse sound of a creeper right next to them.", sound("entity.creeper.primed")),
    prank("warden", "Warden sonic boom", "Sound", "A very loud boom. Needs Minecraft 1.19 or newer.", sound("entity.warden.sonic_boom")),
    prank("ghast", "Ghast scream", "Sound", "A ghast screaming from nowhere.", sound("entity.ghast.scream")),
    prank("enderman", "Enderman stare", "Sound", "The enderman 'you looked at me' scream.", sound("entity.enderman.scream")),
    prank("cave", "Cave noise", "Sound", "One of those unsettling cave ambience sounds.", sound("ambient.cave")),
    prank("bell", "Church bell", "Sound", "A single bell toll.", sound("block.bell.use")),
    prank("heartbeat", "Heartbeat", "Sound", "Six warden heartbeats, getting on their nerves.", b_heartbeat, seconds=5),
    prank("knock", "Knock knock", "Sound", "Three knocks on a wooden door.", b_knock, seconds=2),
    prank("footsteps", "Footsteps behind you", "Sound", "Steps that get closer, from behind them.", b_footsteps, seconds=4),
    prank("blindness", "Blindness", "Effect", "Screen goes black for a few seconds.", effect("blindness", 5), [field("seconds", "Seconds", "number", 5, min=1, max=30)], seconds=5),
    prank("darkness", "Darkness", "Effect", "The screen pulses dark. Needs 1.19 or newer.", effect("darkness", 8), [field("seconds", "Seconds", "number", 8, min=1, max=30)], seconds=8),
    prank("nausea", "Wobbly screen", "Effect", "The nausea wobble, with no actual damage.", effect("nausea", 8), [field("seconds", "Seconds", "number", 8, min=1, max=30)], seconds=8),
    prank("slowness", "Slow motion", "Effect", "They walk through honey for a while.", effect("slowness", 8, 3), [field("seconds", "Seconds", "number", 8, min=1, max=30)], seconds=8),
    prank("speed", "Zoomies", "Effect", "Very fast walking.", effect("speed", 10, 5), [field("seconds", "Seconds", "number", 10, min=1, max=30)], seconds=10),
    prank("jump", "Moon jump", "Effect", "Giant jumps, with slow falling so landing does not hurt.", effect("jump_boost", 10, 6, "slow_falling"), [field("seconds", "Seconds", "number", 10, min=1, max=30)], seconds=10),
    prank("levitate", "Float away", "Effect", "A short lift into the air, then a soft landing.", effect("levitation", 2, 1, "slow_falling"), [field("seconds", "Seconds", "number", 2, min=1, max=5)], seconds=2),
    prank("glow", "Glow outline", "Effect", "Everyone can see them through walls.", effect("glowing", 10), [field("seconds", "Seconds", "number", 10, min=1, max=30)], seconds=10),
    prank("lag", "Fake lag spike", "Effect", "Short freezes that feel like the server is lagging.", b_lag, seconds=4),
    prank("spin", "Spin around", "Movement", "A quick 360 degree turn.", b_spin, seconds=2),
    prank("look_up", "Look up", "Movement", "Snaps their view straight up.", b_look_up),
    prank("look_down", "Look down", "Movement", "Snaps their view straight down.", b_look_down),
    prank("tp_to", "Teleport to a player", "Movement", "Moves them next to another player.", b_tp_to, [field("target", "Player name")]),
    prank("chickens", "Chicken rain", "Spawn", "Ten harmless chickens fall from the sky.", b_chickens, seconds=2),
    prank("bats", "Bat swarm", "Spawn", "A dozen bats appear around them.", b_bats, seconds=2),
    prank("hearts", "Hearts", "Particles", "A burst of hearts above them.", particle("heart", 25, 0.6)),
    prank("poof", "Smoke poof", "Particles", "A puff of cloud around them.", particle("cloud", 40, 0.5, 0.05)),
    prank("totem", "Totem pop", "Particles", "The totem particles and sound, without using one.", b_totem),
    prank("haunting", "Haunting", "Combos", "Darkness, a cave noise, heartbeats, footsteps, a creeper hiss, then 'Behind you.'", b_haunting, seconds=10),
    prank("herobrine", "Herobrine visit", "Combos", "Herobrine joins, says one line, darkens their screen and leaves.", b_herobrine, scope="global", seconds=7),
    prank("calm", "Calm them down", "Cleanup", "Clears effects, stops sounds and removes titles.", b_calm),
]
BY_ID = {item["id"]: item for item in PRANKS}


def public_catalog() -> list:
    return [{k: v for k, v in item.items() if k != "build"} for item in PRANKS]


def build_timeline(prank_id: str, targets: list, params: dict) -> list:
    item = BY_ID.get(prank_id)
    if not item:
        raise ValueError("Unknown prank")
    # A global prank (a broadcast) runs once; it uses the one chosen player's name, or @a for several.
    names = targets if item["scope"] == "player" else ([targets[0]] if len(targets) == 1 else ["@a"])
    steps = []
    for name in names:
        for when, command in item["build"](name, params):
            steps.append((float(when), command))
    if any(has_line_break(command) for _, command in steps):
        raise ValueError("Line breaks are not allowed")
    if steps and max(when for when, _ in steps) > MAX_TIMELINE_SECONDS:
        raise ValueError("That prank runs too long")
    return sorted(steps, key=lambda step: step[0])


def _deliver(server_id: str, command: str):
    settings = rcon_settings(load_meta(server_id) or {})
    if settings["enabled"] and settings["password"]:
        try:
            with Rcon("127.0.0.1", settings["port"], settings["password"]) as rcon:
                rcon.command(command)
            return
        except (OSError, RconError, struct.error):
            pass
    send_command(server_id, command)


def _run(server_id: str, steps: list):
    try:
        start = threading.Event()
        elapsed = 0.0
        for when, command in steps:
            if when > elapsed:
                start.wait(when - elapsed)
                elapsed = when
            if not is_running(server_id):
                return
            _deliver(server_id, command)
    finally:
        with _pending_lock:
            _pending[server_id] = max(0, _pending.get(server_id, 1) - 1)


@app.route("/api/server/<sid>/pranks")
def api_pranks(sid):
    return jsonify({"ok": True, "pranks": public_catalog()})


@app.route("/api/server/<sid>/prank", methods=["POST"])
def api_prank(sid):
    data = request.get_json(silent=True) or {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    raw_targets = data.get("players") if isinstance(data.get("players"), list) else []
    targets = []
    for name in raw_targets[:MAX_TARGETS]:
        name = str(name).strip()
        if not PLAYER_NAME_PATTERN.fullmatch(name):
            return jsonify({"ok": False, "error": f"'{name}' is not a valid player name"}), 400
        if name not in targets:
            targets.append(name)
    if not targets:
        return jsonify({"ok": False, "error": "Pick at least one player"}), 400
    if not is_running(sid):
        return jsonify({"ok": False, "error": "The server must be running"}), 409
    try:
        steps = build_timeline(str(data.get("prank")), targets, params)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _pending_lock:
        if _pending.get(sid, 0) >= MAX_PENDING_PER_SERVER:
            return jsonify({"ok": False, "error": "Too many pranks running at once. Wait a few seconds."}), 429
        _pending[sid] = _pending.get(sid, 0) + 1
    threading.Thread(target=_run, args=(sid, steps), daemon=True, name="prank").start()
    audit(current_account()["username"], f"prank {data.get('prank')}", server=sid, players=",".join(targets))
    return jsonify({"ok": True, "commands": len(steps), "seconds": max((when for when, _ in steps), default=0)})
