"""In-memory runtime state: running processes, console buffers, background jobs."""

import threading

running_servers = {}
console_logs = {}
console_dropped = {}
active_players = {}
playit_processes = {}
playit_logs = {}
import_sessions = {}
backup_jobs = {}
update_cache = {}

map_cache = {}
audit_lock = threading.Lock()

# Called as hook(server_id, exit_code, crashed, deliberate) when a Minecraft process ends.
exit_hooks = []
started_at = {}
crash_times = {}
# True after a manager update has been installed but the new code is not running yet.
restart_flags = {"pending": False, "auto": False}
java_install = {"state": "idle", "message": "", "log": []}
