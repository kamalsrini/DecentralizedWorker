"""Manifest read/write/validate helpers."""

import json
from pathlib import Path


def load_manifest(path: Path) -> dict:
    """Load and return the task manifest."""
    with open(path, "r") as f:
        return json.load(f)


def save_manifest(path: Path, data: dict) -> None:
    """Write the task manifest."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def find_task(manifest: dict, task_id: str) -> dict | None:
    """Find a task by ID in the manifest."""
    for task in manifest.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


def get_task_stats(manifest: dict) -> dict:
    """Compute summary statistics from the manifest."""
    tasks = manifest.get("tasks", [])
    total = len(tasks)
    statuses = {}
    for t in tasks:
        s = t.get("status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1
    return {"total": total, **statuses}
