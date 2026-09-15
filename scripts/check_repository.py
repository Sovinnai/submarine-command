#!/usr/bin/env python3
"""Locally check that tracked files exclude live saves, archives and CI workflows."""
from pathlib import PurePosixPath
import subprocess
import sys

tracked = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
bad = []
for name in filter(None, tracked):
    path = PurePosixPath(name)
    forbidden_directory = any(part in {".sessions", "sessions", "session", "debriefs", "checkpoints", "orders"} for part in path.parts)
    forbidden_file = path.name in {"private.json", "public.json", "session.lock", "Patrol_Brief.md", "CAPTAIN_NOTES.md", ".env"}
    forbidden_archive = path.suffix.lower() in {".zip", ".bundle"}
    forbidden_workflow = name.startswith(".github/workflows/")
    if forbidden_directory or forbidden_file or forbidden_archive or forbidden_workflow or path.name.startswith(".env.") and path.name != ".env.example":
        bad.append(name)
if bad:
    print("Remove live-session files, generated archives or CI workflows from the Git index:")
    for name in bad:
        print(name)
    sys.exit(1)
print("Tracked files contain no live-session files, generated archives or GitHub Actions workflows.")
