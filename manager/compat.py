"""Which plugin and mod files a server can actually load, judged from its type and from what is inside each jar.

A Paper or Purpur server only loads plugins (a plugin.yml or paper-plugin.yml inside); a Fabric, Forge or NeoForge
server only loads mods built for that loader. A mod dropped into a plugins folder is not a mild problem: Paper logs a
stack trace for every one of them at start-up. Modrinth lists one project under several loaders, so choosing "the newest
version" without a loader filter is how those files get there; this module names the loaders a server accepts and
recognises the wrong ones."""

import io
import re
import zipfile
from functools import lru_cache
from pathlib import Path

# Modrinth loader names whose files run on a given server type, best match first.
PLUGIN_LOADERS = {"purpur": ["purpur", "paper", "spigot", "bukkit"], "paper": ["paper", "spigot", "bukkit"]}
MOD_LOADERS = {"fabric": ["fabric"], "forge": ["forge"], "neoforge": ["neoforge"]}
ACCEPTED_MODS = {"fabric": {"fabric"}, "forge": {"forge"}, "neoforge": {"neoforge", "forge"}}  # NeoForge for 1.20.1 still runs Forge mods
DESCRIPTORS = {"plugin.yml": "plugin", "paper-plugin.yml": "plugin", "fabric.mod.json": "fabric", "quilt.mod.json": "quilt",
               "META-INF/neoforge.mods.toml": "neoforge", "META-INF/mods.toml": "forge"}
KIND_NAMES = {"plugin": "Bukkit plugin", "fabric": "Fabric mod", "quilt": "Quilt mod", "forge": "Forge mod", "neoforge": "NeoForge mod"}
FOLIA_NAME = re.compile(r"(?<![a-z])folia(?![a-z])", re.I)
MAX_DESCRIPTOR_BYTES = 64 * 1024


def loaders_for(server_type, folder: str) -> list:
    """The Modrinth loaders to ask for when installing into `folder` ("plugins" or "mods") of this kind of server. Empty means no opinion."""
    return list((PLUGIN_LOADERS if folder == "plugins" else MOD_LOADERS).get(str(server_type or "").lower(), []))


def _depends(text: str) -> tuple:
    """Hard dependencies from a plugin.yml, without a YAML library: inline list, single value or block list."""
    match = re.search(r"^depend\s*:[ \t]*(.*)$", text, re.M)
    if not match:
        return ()
    rest = match.group(1).split("#", 1)[0].strip()
    if rest.startswith("["):
        names = rest.strip("[]").split(",")
    elif rest:
        names = [rest]
    else:
        names = []
        for line in text[match.end():].splitlines():
            item = re.match(r"^\s+-\s*(\S.*?)\s*(?:#.*)?$", line)
            if item:
                names.append(item.group(1))
            elif line.strip() and not line.lstrip().startswith("#"):
                break  # the next key
    return tuple(name.strip().strip("'\"") for name in names if name.strip().strip("'\""))


def _facts(handle):
    """(kinds, plugin name, hard dependencies, broken) for a jar opened as a file object."""
    try:
        with zipfile.ZipFile(handle) as jar:
            names = set(jar.namelist())
            kinds = frozenset(kind for descriptor, kind in DESCRIPTORS.items() if descriptor in names)
            plugin_name, depends = "", ()
            descriptor = next((d for d in ("plugin.yml", "paper-plugin.yml") if d in names), None)
            if descriptor and jar.getinfo(descriptor).file_size <= MAX_DESCRIPTOR_BYTES:
                text = jar.read(descriptor).decode("utf-8", "replace")
                name = re.search(r"^name\s*:[ \t]*['\"]?([^'\"\r\n#]+?)['\"]?[ \t]*(?:#.*)?$", text, re.M)
                plugin_name = name.group(1).strip() if name else ""
                depends = _depends(text) if descriptor == "plugin.yml" else ()
            return kinds, plugin_name, depends, False
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return frozenset(), "", (), True


@lru_cache(maxsize=1024)
def _file_facts(path: str, mtime_ns: int, size: int):
    with open(path, "rb") as handle:
        return _facts(handle)


def jar_facts(path: Path):
    try:
        info = path.stat()
        return _file_facts(str(path), info.st_mtime_ns, info.st_size)
    except OSError:
        return frozenset(), "", (), True


def _kind_problem(kinds, broken: bool, name: str, server_type: str, folder: str):
    """Why a jar cannot run on this server, from its type alone (not from what else is installed), or None."""
    server_type = str(server_type or "").lower()
    label = server_type.title()
    if folder == "plugins" and server_type in PLUGIN_LOADERS:
        if broken:
            return "Not a valid .jar file (the download may have been cut short)"
        if "plugin" not in kinds:
            built_for = next((KIND_NAMES[k] for k in ("neoforge", "forge", "fabric", "quilt") if k in kinds), None)
            return f"This is a {built_for}, not a plugin, so {label} cannot load it" if built_for else "Not a plugin (it has no plugin.yml)"
        if FOLIA_NAME.search(name):
            return f"A Folia-only build, and this is a {label} server. Use the Bukkit or Paper build"
    elif folder == "mods" and server_type in MOD_LOADERS:
        if broken:
            return "Not a valid .jar file (the download may have been cut short)"
        mod_kinds = kinds & {"fabric", "quilt", "forge", "neoforge"}
        if mod_kinds and not (kinds & ACCEPTED_MODS[server_type]):
            return f"This is a {KIND_NAMES[sorted(mod_kinds)[0]]}, and this is a {label} server"
        if "plugin" in kinds and not mod_kinds:
            return f"This is a Bukkit plugin, not a mod, so {label} cannot load it"
    return None


def jar_problem(path: Path, server_type: str, folder: str):
    kinds, _, _, broken = jar_facts(path)
    return _kind_problem(kinds, broken, path.name.lower().removesuffix(".disabled"), server_type, folder)


def bytes_problem(content: bytes, filename: str, server_type: str, folder: str):
    """The same check for a download that has not been written to disk yet."""
    kinds, _, _, broken = _facts(io.BytesIO(content))
    return _kind_problem(kinds, broken, filename.lower(), server_type, folder)


def folder_problems(directory: Path, server_type: str, folder: str) -> dict:
    """{file name: reason} for the enabled jars in a plugins or mods folder that the server cannot load.
    Beyond the wrong-loader checks, a plugin whose hard dependency is not installed is named too."""
    problems = {}
    if not directory.is_dir():
        return problems
    known = {}
    for jar in sorted(directory.iterdir()):
        if not (jar.is_file() and jar.name.lower().endswith(".jar")):
            continue  # disabled files are not loaded, so they cannot fail
        reason = jar_problem(jar, server_type, folder)
        if reason:
            problems[jar.name] = reason
        else:
            known[jar] = jar_facts(jar)
    if folder == "plugins" and str(server_type or "").lower() in PLUGIN_LOADERS:
        installed = {facts[1].lower() for facts in known.values() if facts[1]}
        for jar, (_, _, depends, _) in known.items():
            missing = [name for name in depends if name.lower() not in installed]
            if missing:
                problems[jar.name] = f"Needs {', '.join(missing)}, which is not installed (the server will refuse to load it)"
        # Two jars of the same plugin: Paper stops with "Ambiguous plugin name" for the second. Keep the newest one.
        by_name = {}
        for jar, facts in known.items():
            if facts[1]:
                by_name.setdefault(facts[1].lower(), []).append(jar)
        for jars in by_name.values():
            if len(jars) > 1:
                newest = max(jars, key=lambda j: j.stat().st_mtime)
                for jar in jars:
                    if jar is not newest and jar.name not in problems:
                        problems[jar.name] = f"The same plugin as {newest.name}: the server loads only one of them. Disable the older file"
    return problems
