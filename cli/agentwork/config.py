"""Configuration and path constants for the AgentWork CLI."""

import json
import os
from pathlib import Path


def find_repo_root(start: str | None = None) -> Path:
    """Walk up from start (or cwd) until we find tasks/manifest.json."""
    current = Path(start) if start else Path.cwd()
    while current != current.parent:
        if (current / "tasks" / "manifest.json").exists():
            return current
        current = current.parent
    raise FileNotFoundError(
        "Not an AgentWork project. Run 'agentwork init' first or cd into a project directory."
    )


class Config:
    """Resolved paths for the current AgentWork project."""

    def __init__(self, root: Path | None = None):
        self.root = root or find_repo_root()
        self.manifest_path = self.root / "tasks" / "manifest.json"
        self.ledger_path = self.root / "reputation" / "ledger.json"
        self.output_dir = self.root / "output" / "sections"
        self.audits_dir = self.root / "audits"
        self.retros_dir = self.root / "retros"
        self.source_dir = self.root / "source"
        self.schemas_dir = self.root / "schemas"

    def load_manifest(self) -> dict:
        with open(self.manifest_path, "r") as f:
            return json.load(f)

    def save_manifest(self, data: dict) -> None:
        with open(self.manifest_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    def load_ledger(self) -> dict:
        if not self.ledger_path.exists():
            return {"version": "1.0.0", "agents": {}, "last_updated": None}
        with open(self.ledger_path, "r") as f:
            return json.load(f)

    def save_ledger(self, data: dict) -> None:
        with open(self.ledger_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
