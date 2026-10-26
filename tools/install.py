"""Install companion files with backups; never remove unrelated add-on files."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil


def install(user_dir: Path, apply: bool = False) -> dict:
    user_dir = user_dir.expanduser().resolve()
    if not user_dir.is_dir():
        raise ValueError("Pass the existing directory returned by FreeCAD.getUserAppDataDir()")
    source = Path(__file__).resolve().parents[1] / "src" / "freecad_codex"
    destination = user_dir / "Mod" / "ArthurCodexCompanion"
    files = sorted(source.glob("*.py"))
    if not files or not (source / "InitGui.py").is_file():
        raise RuntimeError("Companion source is incomplete")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = user_dir / "CodexSidebarBackups" / stamp
    result = {"applied": apply, "destination": str(destination), "backup": None,
              "files": [p.name for p in files]}
    if not apply:
        return result
    # Validate the complete destination before changing any existing file.
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError("Unexpected installation directory: " + str(destination))
    for item in files:
        target = destination / item.name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("Unexpected installation entry: " + str(target))
    destination.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for item in files:
        target = destination / item.name
        if target.exists():
            shutil.copy2(target, backup / item.name)
    # All old files are backed up before the first replacement.
    for item in files:
        target = destination / item.name
        shutil.copy2(item, target)
        digest = hashlib.sha256(item.read_bytes()).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Installed hash mismatch: " + item.name)
        hashes[item.name] = digest
    result.update(backup=str(backup), sha256=hashes)
    (backup / "install-manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(args.user_dir, args.apply), indent=2))


if __name__ == "__main__":
    main()
