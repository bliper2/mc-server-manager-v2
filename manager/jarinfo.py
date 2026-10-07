"""What a plugin or mod jar says about itself: name, version, description and authors, read from plugin.yml,
paper-plugin.yml, fabric.mod.json, quilt.mod.json or mods.toml / neoforge.mods.toml inside the jar."""

import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

from . import yamlite

MAX_DESCRIPTOR_BYTES = 64 * 1024
EMPTY = {"name": "", "version": "", "description": "", "authors": [], "website": "", "api": ""}


def _clean(value, limit=240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _toml_mod_block(text: str) -> str:
    start = re.search(r"^\[\[mods\]\]", text, re.M)
    if not start:
        return ""
    rest = text[start.end():]
    stop = re.search(r"^\[", rest, re.M)
    return rest[:stop.start()] if stop else rest


TOML_VALUE = r"""[ \t]*=[ \t]*(?:'''(.*?)'''|\"\"\"(.*?)\"\"\"|"([^"\n]*)"|'([^'\n]*)')"""


def _toml_value(block: str, key: str) -> str:
    match = re.search(r"^[ \t]*" + re.escape(key) + TOML_VALUE, block, re.M | re.S)
    return next((g for g in match.groups() if g is not None), "") if match else ""


def _manifest_version(jar) -> str:
    try:
        text = jar.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except KeyError:
        return ""
    match = re.search(r"^Implementation-Version:\s*(\S+)", text, re.M)
    return match.group(1) if match else ""


def read_info(handle, filename: str) -> dict:
    info = dict(EMPTY, authors=[])
    try:
        with zipfile.ZipFile(handle) as jar:
            names = set(jar.namelist())

            def text(entry):
                if jar.getinfo(entry).file_size > MAX_DESCRIPTOR_BYTES:
                    return ""
                return jar.read(entry).decode("utf-8", "replace")

            descriptor = next((d for d in ("plugin.yml", "paper-plugin.yml") if d in names), None)
            if descriptor:
                body = text(descriptor)
                top = yamlite.top_level(body)
                authors = yamlite.block_list(body, "authors") or yamlite.block_list(body, "author")
                info.update(name=top.get("name", ""), version=top.get("version", ""), description=top.get("description", ""), authors=authors,
                            website=top.get("website", ""), api=top.get("api-version", ""))
            elif "fabric.mod.json" in names:
                data = json.loads(text("fabric.mod.json"))
                authors = [a.get("name", "") if isinstance(a, dict) else str(a) for a in data.get("authors", [])]
                info.update(name=data.get("name") or data.get("id", ""), version=str(data.get("version", "")), description=data.get("description", ""), authors=authors,
                            website=(data.get("contact") or {}).get("homepage", ""))
            elif "quilt.mod.json" in names:
                loader = json.loads(text("quilt.mod.json")).get("quilt_loader", {})
                meta = loader.get("metadata", {})
                info.update(name=meta.get("name") or loader.get("id", ""), version=str(loader.get("version", "")), description=meta.get("description", ""),
                            authors=list((meta.get("contributors") or {}).keys()))
            else:
                toml = next((t for t in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml") if t in names), None)
                if toml:
                    block = _toml_mod_block(text(toml))
                    version = _toml_value(block, "version")
                    if "${" in version:
                        version = _manifest_version(jar)
                    info.update(name=_toml_value(block, "displayName") or _toml_value(block, "modId"), version=version, description=_toml_value(block, "description"),
                                authors=[a.strip() for a in re.split(r"[,;]", _toml_value(block, "authors")) if a.strip()], website=_toml_value(block, "displayURL"))
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        pass
    base = re.sub(r"(\.disabled)?$", "", filename, flags=re.I)
    base = re.sub(r"\.jar$", "", base, flags=re.I)
    if not info["version"]:
        guess = re.search(r"(\d+(?:\.\d+)+[\w.+-]*)", base)
        info["version"] = guess.group(1).rstrip(".-") if guess else ""
    info["name"] = _clean(info["name"], 80) or re.sub(r"[-_.]?\d.*$", "", base) or filename
    info["version"] = _clean(info["version"], 40)
    info["description"] = _clean(info["description"])
    info["website"] = _clean(info["website"], 200)
    info["api"] = _clean(info["api"], 20)
    info["authors"] = [_clean(a, 40) for a in info["authors"] if _clean(a, 40)][:6]
    return info


@lru_cache(maxsize=1024)
def _file_info(path: str, mtime_ns: int, size: int) -> str:
    with open(path, "rb") as handle:
        return json.dumps(read_info(handle, Path(path).name))


def plugin_info(path: Path) -> dict:
    """{name, version, description, authors, website, api} read from the jar itself."""
    try:
        stat = path.stat()
        return json.loads(_file_info(str(path), stat.st_mtime_ns, stat.st_size))
    except OSError:
        return dict(EMPTY, name=path.stem, authors=[])
