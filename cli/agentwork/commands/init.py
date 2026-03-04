"""agentwork init — Initialize or verify an AgentWork project."""

import subprocess
import sys
from pathlib import Path

from agentwork.config import Config


def run(args):
    if args.repo:
        # Clone the repo
        print(f"Cloning {args.repo}...")
        try:
            subprocess.run(["gh", "repo", "clone", args.repo], check=True)
        except subprocess.CalledProcessError:
            print("\033[31mFailed to clone repo. Is 'gh' installed and authenticated?\033[0m")
            sys.exit(1)

    try:
        cfg = Config()
    except FileNotFoundError:
        print("\033[31mNot an AgentWork project directory.\033[0m")
        print("Run from a directory containing tasks/manifest.json, or use --repo to clone.")
        sys.exit(1)

    manifest = cfg.load_manifest()

    # Verify structure
    checks = [
        ("tasks/manifest.json", cfg.manifest_path.exists()),
        ("output/schema.json", (cfg.root / "output" / "schema.json").exists()),
        ("reputation/ledger.json", cfg.ledger_path.exists()),
        ("source/", cfg.source_dir.exists()),
        ("audits/", cfg.audits_dir.exists()),
        ("retros/", cfg.retros_dir.exists()),
    ]

    print(f"\n\033[1mAgentWork Project: {manifest.get('project', 'unknown')}\033[0m")
    print(f"Root: {cfg.root}\n")

    all_ok = True
    for name, ok in checks:
        icon = "\033[32m+\033[0m" if ok else "\033[31m-\033[0m"
        if not ok:
            all_ok = False
        print(f"  {icon} {name}")

    tasks = manifest.get("tasks", [])
    print(f"\n  Tasks: {len(tasks)}")
    print(f"  Version: {manifest.get('version', '?')}")

    if all_ok:
        print("\n\033[32mProject structure verified.\033[0m")
    else:
        print("\n\033[33mSome files are missing. The project may not be fully set up.\033[0m")
