"""Background automation: crash recovery, scheduled restarts, announcements, maintenance, and their routes."""

import re
import threading
import time
from datetime import datetime

from flask import jsonify, request

from . import app
from .auth import require_owner
from .backups import auto_backup_settings, auto_update_settings, run_backup_task, start_job
from . import metrics
from .config import MAINTENANCE_INTERVAL, SERVERS_DIR
from .javatools import java_status, start_java_install
from .lifecycle import begin_restart, resume_servers, running_server_ids, supervised
from .notify import notify, public_settings, send_test, update_settings
from .ops import LOW_DISK_MB, disk_free_mb
from .procs import ping_minecraft_server, send_command, start_server, stop_server
from .providers import apply_plugin_update, scan_plugin_updates
from .state import active_players, backup_jobs, crash_times, exit_hooks, restart_flags
from .store import audit, get_server_path, is_running, load_meta, save_meta
from .updater import check_manager_update

SCHEDULER_TICK = 15
CPU_ALERT_PERCENT = 90
RAM_ALERT_PERCENT = 92
ALERT_SAMPLES = 8          # consecutive 15 s samples, i.e. two minutes of sustained load
ALERT_COOLDOWN = 1800
HUNG_AFTER_SECONDS = 180   # grace period for world generation after a start
HUNG_CHECKS = 5            # consecutive one-minute checks without an answer
_watch: dict = {"high": {}, "hung": {}, "alerted": {}}
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULT_AUTOMATION = {
    "auto_restart": {"enabled": False, "max_tries": 3, "window_minutes": 10},
    "schedule_restart": {"enabled": False, "time": "04:00", "warn_minutes": 5},
    "announcements": {"enabled": False, "interval_minutes": 30, "messages": []},
}
_schedule_memory: dict = {}  # server id -> {"day", "warned", "last_announce", "next_message"}


def _clamp(value, low, high, fallback):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def automation_settings(meta: dict) -> dict:
    """Per-server automation, always complete and within safe bounds."""
    stored = meta.get("automation") if isinstance(meta.get("automation"), dict) else {}
    out = {key: dict(value) for key, value in DEFAULT_AUTOMATION.items()}
    restart = stored.get("auto_restart") if isinstance(stored.get("auto_restart"), dict) else {}
    out["auto_restart"] = {
        "enabled": bool(restart.get("enabled")),
        "max_tries": _clamp(restart.get("max_tries", 3), 1, 10, 3),
        "window_minutes": _clamp(restart.get("window_minutes", 10), 1, 120, 10),
    }
    schedule = stored.get("schedule_restart") if isinstance(stored.get("schedule_restart"), dict) else {}
    when = str(schedule.get("time") or "04:00")
    out["schedule_restart"] = {
        "enabled": bool(schedule.get("enabled")),
        "time": when if TIME_PATTERN.match(when) else "04:00",
        "warn_minutes": _clamp(schedule.get("warn_minutes", 5), 1, 60, 5),
    }
    notes = stored.get("announcements") if isinstance(stored.get("announcements"), dict) else {}
    messages = [str(m).strip()[:200] for m in (notes.get("messages") or []) if str(m).strip() and "\n" not in str(m) and "\r" not in str(m)]
    out["announcements"] = {
        "enabled": bool(notes.get("enabled")),
        "interval_minutes": _clamp(notes.get("interval_minutes", 30), 5, 1440, 30),
        "messages": messages[:10],
    }
    return out


def restart_server(server_id: str, reason: str):
    """Stop (saving the world), then start again. Used by the schedule and crash recovery."""
    meta = load_meta(server_id)
    ram = _clamp(meta.get("ram", 2048), 512, 65536, 2048)
    if is_running(server_id):
        send_command(server_id, "save-all flush")
        stop_server(server_id, announce=False)
    ok, message = start_server(server_id, ram, automatic=True)
    audit("system", f"restart ({reason})" if ok else f"restart failed: {message}", server=server_id)
    notify("server_restart" if ok else "server_crash", f"{reason}" if ok else f"Restart failed: {message}", server_id)
    return ok, message


def handle_server_exit(server_id: str, code, crashed: bool, deliberate: bool):
    """Called by procs when a Minecraft process ends. Crashes can be restarted automatically, within a limit."""
    if deliberate:
        return
    if not crashed:
        audit("system", "server shut down", server=server_id, exit_code=code)
        notify("server_stop", "Shut down from the console or in game", server_id)
        return
    audit("system", "server crashed", server=server_id, exit_code=code)
    notify("server_crash", f"The process ended unexpectedly (exit code {code}).", server_id)
    cfg = automation_settings(load_meta(server_id))["auto_restart"]
    if not cfg["enabled"]:
        return
    now = time.time()
    recent = [t for t in crash_times.get(server_id, []) if now - t < cfg["window_minutes"] * 60]
    recent.append(now)
    crash_times[server_id] = recent
    if len(recent) > cfg["max_tries"]:
        audit("system", "auto-restart gave up", server=server_id)
        notify("server_crash", f"Gave up restarting after {cfg['max_tries']} crashes in {cfg['window_minutes']} minutes. It needs a human.", server_id)
        return
    threading.Thread(target=_delayed_restart, args=(server_id, len(recent), cfg["max_tries"]), daemon=True).start()


def _delayed_restart(server_id: str, attempt: int, limit: int):
    time.sleep(5)  # let the port and world lock release
    if not is_running(server_id):
        restart_server(server_id, f"Automatic restart after a crash (attempt {attempt} of {limit})")


exit_hooks.append(handle_server_exit)


def _plural(minutes: int) -> str:
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def scheduler_tick(now: datetime | None = None):
    """Runs every few seconds: scheduled restarts with in-game warnings, and rotating announcements."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    for folder in SERVERS_DIR.iterdir():
        if not folder.is_dir() or not (folder / "manager_meta.json").exists():
            continue
        sid = folder.name
        try:
            cfg = automation_settings(load_meta(sid))
        except (OSError, ValueError):
            continue
        if not is_running(sid):
            continue
        memory = _schedule_memory.setdefault(sid, {"day": today, "warned": set(), "restarted": None, "last_announce": time.time(), "next_message": 0})
        if memory["day"] != today:
            memory.update(day=today, warned=set())
        schedule = cfg["schedule_restart"]
        if schedule["enabled"]:
            hour, minute = (int(part) for part in schedule["time"].split(":"))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            minutes_left = (target - now).total_seconds() / 60
            for threshold in sorted({schedule["warn_minutes"], 1}, reverse=True):
                if 0 < minutes_left <= threshold and threshold not in memory["warned"]:
                    memory["warned"].add(threshold)
                    send_command(sid, f"say Server restarting in {_plural(threshold)}. Finish up what you are doing.")
            # A restart is due once the time has passed, but only within ten minutes of it, so starting the manager at
            # 15:00 never triggers a 04:00 restart.
            if minutes_left <= 0 and -10 < minutes_left and memory["restarted"] != today:
                memory["restarted"] = today
                threading.Thread(target=restart_server, args=(sid, f"Scheduled restart at {schedule['time']}"), daemon=True).start()
                continue
        notes = cfg["announcements"]
        if notes["enabled"] and notes["messages"] and active_players.get(sid):
            if time.time() - memory["last_announce"] >= notes["interval_minutes"] * 60:
                memory["last_announce"] = time.time()
                message = notes["messages"][memory["next_message"] % len(notes["messages"])]
                memory["next_message"] += 1
                send_command(sid, f"say {message}")


def _alert_due(key, cooldown, now) -> bool:
    if now - _watch["alerted"].get(key, 0) < cooldown:
        return False
    _watch["alerted"][key] = now
    return True


def run_watchers(tick: int, now: float | None = None, collect=metrics.collect, ping=ping_minecraft_server, free_mb=disk_free_mb):
    """Quiet health checks that only speak up (Discord and the activity log) when something stays wrong."""
    now = time.time() if now is None else now
    snapshot = collect()["servers"]
    for sid in [s for s in _watch["high"] if s.split("|")[0] not in snapshot]:
        _watch["high"].pop(sid, None)
    for sid, stat in snapshot.items():
        meta = load_meta(sid)
        limit = _clamp(meta.get("ram", 2048), 512, 65536, 2048)
        readings = (("cpu", stat.get("cpu"), CPU_ALERT_PERCENT, "CPU"),
                    ("ram", None if stat.get("ram_mb") is None else stat["ram_mb"] / limit * 100, RAM_ALERT_PERCENT, "Memory"))
        for kind, value, threshold, label in readings:
            key = f"{sid}|{kind}"
            if value is not None and value >= threshold:
                _watch["high"][key] = _watch["high"].get(key, 0) + 1
            else:
                _watch["high"][key] = 0
            if _watch["high"][key] >= ALERT_SAMPLES and _alert_due(key, ALERT_COOLDOWN, now):
                audit("system", f"{label.lower()} above {threshold}% for two minutes", server=sid)
                notify("resource_alert", f"{label} has been above {threshold}% for two minutes ({value:.0f}%).", sid)
        if tick % 4 == 0 and stat["uptime"] > HUNG_AFTER_SECONDS:  # once a minute
            answered = ping(meta.get("port", 25565)) is not None
            _watch["hung"][sid] = 0 if answered else _watch["hung"].get(sid, 0) + 1
            if _watch["hung"][sid] >= HUNG_CHECKS and _alert_due(f"{sid}|hung", 3600, now):
                audit("system", "server not answering", server=sid)
                notify("server_hung", f"The process is running but port {meta.get('port', 25565)} has not answered for {HUNG_CHECKS} minutes.", sid)
    if tick % 40 == 0:  # every ten minutes
        free = free_mb()["free_mb"]
        if free is not None and free < LOW_DISK_MB and _alert_due("disk", 86400, now):
            notify("disk_low", f"Only {free // 1024} GB left on the drive that holds the servers and backups.")


def scheduler_loop():
    tick = 0
    while True:
        time.sleep(SCHEDULER_TICK)
        tick += 1
        try:
            scheduler_tick()
            run_watchers(tick)
        except Exception as exc:
            print("Scheduler error:", exc)


def run_scheduled_maintenance():
    now = time.time()
    check_manager_update()
    if restart_flags.get("auto") and restart_flags["pending"] and supervised() and not running_server_ids():
        restart_flags["auto"] = False
        begin_restart("system", "automatic update installed")
        return
    for folder in SERVERS_DIR.iterdir():
        if not folder.is_dir() or not (folder / "manager_meta.json").exists():
            continue
        sid = folder.name
        meta = load_meta(sid)
        dirty = False

        backup_cfg = auto_backup_settings(meta)
        if backup_cfg["enabled"]:
            due_at = meta.get("last_auto_backup_at", 0) + backup_cfg["interval_hours"] * 3600
            job = backup_jobs.get(sid)
            if now >= due_at and not (job and job.get("state") == "running"):
                keep = int(meta.get("backup_keep", 10) or 10)
                started, _ = start_job(sid, run_backup_task, "Automatic backup", backup_cfg["world"], keep, True)
                if started:
                    meta["last_auto_backup_at"] = now
                    meta["last_auto_backup"] = datetime.now().isoformat(timespec="seconds")
                    dirty = True

        update_cfg = auto_update_settings(meta)
        if update_cfg["enabled"]:
            due_at = meta.get("last_update_check_at", 0) + update_cfg["interval_hours"] * 3600
            if now >= due_at:
                try:
                    items = scan_plugin_updates(sid)
                    meta["last_update_check_at"] = now
                    meta["last_update_check"] = datetime.now().isoformat(timespec="seconds")
                    dirty = True
                    if update_cfg["install"]:
                        for item in items:
                            if item.get("update_available") and item.get("download_url"):
                                apply_plugin_update(sid, item["folder"], item["file"], item["download_url"], item.get("download_filename"))
                except Exception as exc:
                    print(f"Update check failed for {sid}:", exc)

        if dirty:
            save_meta(sid, meta)


def maintenance_loop():
    while True:
        time.sleep(MAINTENANCE_INTERVAL)
        try:
            run_scheduled_maintenance()
        except Exception as exc:
            print("Maintenance loop error:", exc)


def start_background_threads():
    threading.Thread(target=maintenance_loop, daemon=True, name="maintenance").start()
    threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()
    # Delayed so the web server is up before servers that were running before a restart start booting.
    threading.Timer(3.0, resume_servers).start()


@app.route("/api/server/<sid>/automation", methods=["GET", "POST"])
def api_automation(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    meta = load_meta(sid)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if isinstance(data.get("schedule_restart"), dict) and not TIME_PATTERN.match(str(data["schedule_restart"].get("time") or "")):
            return jsonify({"ok": False, "error": "Restart time must look like 04:00"}), 400
        meta["automation"] = data
        meta["automation"] = automation_settings(meta)
        save_meta(sid, meta)
        return jsonify({"ok": True, "automation": meta["automation"]})
    return jsonify({"ok": True, "automation": automation_settings(meta)})


@app.route("/api/notifications", methods=["GET", "POST"])
def api_notifications():
    denied = require_owner()
    if denied:
        return denied
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        problem = update_settings(data.get("webhook") if "webhook" in data else None, data.get("events"))
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
    return jsonify({"ok": True, **public_settings()})


@app.route("/api/notifications/test", methods=["POST"])
def api_notifications_test():
    denied = require_owner()
    if denied:
        return denied
    problem = send_test()
    return jsonify({"ok": problem is None, "error": problem}), (200 if problem is None else 502)


@app.route("/api/java")
def api_java():
    return jsonify({"ok": True, **java_status(request.args.get("version"))})


@app.route("/api/java/install", methods=["POST"])
def api_java_install():
    denied = require_owner()
    if denied:
        return denied
    started, message = start_java_install()
    return jsonify({"ok": started, "message": message}), (200 if started else 409)
