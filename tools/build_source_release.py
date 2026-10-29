# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors
"""Archive an audited, clean Git commit; refuse local/private runtime files."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import tomllib
import zipfile

ROOT_FILES = {
    "README.md",
    "LICENSE",
    "COPYRIGHT",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "pyproject.toml",
    "uv.lock",
    "启动助手.cmd",
    "启动助手.pyw",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
}
PRIVATE_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "work",
    "sessions",
    "pending-sessions",
    "wechat-web",
    "edge-profile",
    "browser-profile",
}


def public_path(name):
    if "\\" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in PRIVATE_PARTS or p in {".", ".."} for p in path.parts):
        return False
    if name in ROOT_FILES:
        return True
    if len(path.parts) < 2:
        return False
    if path.parts[0] == "activity_check":
        return (len(path.parts) == 2 and path.suffix == ".py") or (
            len(path.parts) == 3 and path.parts[1] == "assets" and path.suffix in {".svg", ".png", ".ico"}
        )
    if path.parts[0] in {"tests", "tools"}:
        return len(path.parts) == 2 and path.suffix == ".py"
    if path.parts[0] == "docs":
        return len(path.parts) == 2 and path.suffix in {".md", ".png"}
    if path.parts[0] == "LICENSES":
        return len(path.parts) == 2 and any(
            word in path.name.lower() for word in ("license", "copying", "notice", "authors")
        )
    return path.parts[0] == ".github" and path.suffix in {".md", ".yml", ".yaml"}


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    return result.stdout


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(root, destination):
    if git(root, "status", "--porcelain"):
        raise RuntimeError("Commit the reviewed source before packaging")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("license") != "AGPL-3.0-only":
        raise RuntimeError("Expected AGPL-3.0-only package metadata")
    version = project["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise RuntimeError("Invalid release version")
    tag = "v" + version
    if os.environ.get("GITHUB_REF_TYPE") == "tag" and os.environ.get("GITHUB_REF_NAME") != tag:
        raise RuntimeError("Version tag does not match package metadata")
    entries = []
    for entry in git(root, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, encoded = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        name = encoded.decode("utf-8")
        if mode not in {"100644", "100755"} or kind != "blob" or not public_path(name):
            raise RuntimeError("Non-public or unsupported tracked file: " + name)
        entries.append(name)
    if not ROOT_FILES.issubset(entries):
        raise RuntimeError("Required source/license files are missing")
    destination.mkdir(parents=True, exist_ok=True)
    stem = "nxg-activity-assistant-" + tag
    archive = destination / (stem + "-source.zip")
    manifest_path = destination / (stem + "-manifest.json")
    checksums = destination / (stem + "-SHA256SUMS.txt")
    with tempfile.TemporaryDirectory(prefix=".nxg-source-", dir=destination) as temp:
        if Path(temp).resolve().parent != destination.resolve() or archive.resolve().parent != destination.resolve():
            raise RuntimeError("Archive output escaped the requested destination")
        temporary = Path(temp) / archive.name
        git(root, "archive", "--format=zip", "--prefix=nxg-activity-assistant/", "--output=" + str(temporary), "HEAD")
        manifest = []
        with zipfile.ZipFile(temporary) as package:
            for item in package.infolist():
                if item.is_dir():
                    continue
                relative = item.filename.removeprefix("nxg-activity-assistant/")
                if not public_path(relative):
                    raise RuntimeError("Unexpected archive entry")
                data = package.read(item)
                manifest.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if {row["path"] for row in manifest} != set(entries):
            raise RuntimeError("Archive does not match the reviewed Git tree")
        temporary.replace(archive)
    manifest_path.write_text(
        json.dumps(
            {
                "project": project["name"],
                "version": version,
                "license": project["license"],
                "commit": git(root, "rev-parse", "HEAD").decode().strip(),
                "files": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksums.write_text("".join(digest(p) + "  " + p.name + "\n" for p in (archive, manifest_path)), encoding="ascii")
    return {
        "archive": str(archive),
        "manifest": str(manifest_path),
        "checksums": str(checksums),
        "files": len(manifest),
        "sha256": digest(archive),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(build(root, args.output_dir.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
