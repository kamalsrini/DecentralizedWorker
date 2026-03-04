"""agentwork accept <pr-number> — Merge a PR and update reputation."""

import json
import subprocess
import sys
from datetime import datetime, timezone

from agentwork.config import Config
from agentwork.manifest import find_task


def run(args):
    cfg = Config()
    manifest = cfg.load_manifest()
    pr_number = args.pr_number

    # Get PR info
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title,body,author,state"],
            capture_output=True, text=True, check=True,
        )
        pr_data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"\033[31mFailed to get PR #{pr_number}: {e}\033[0m")
        sys.exit(1)

    if pr_data.get("state") == "MERGED":
        print(f"\033[33mPR #{pr_number} is already merged.\033[0m")
        sys.exit(0)

    # Extract task_id from PR body
    task_id = None
    agent_id = pr_data.get("author", {}).get("login", "unknown")
    body = pr_data.get("body", "") or ""
    for line in body.split("\n"):
        if "task" in line.lower() and "id" in line.lower():
            parts = line.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip().strip("`").strip('"')
                if candidate:
                    task_id = candidate
                    break

    print(f"  PR #{pr_number}: {pr_data.get('title', '')}")
    print(f"  Agent: {agent_id}")
    print(f"  Task: {task_id or 'unknown'}")

    # Merge PR
    try:
        subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--merge"],
            check=True,
        )
        print(f"\n\033[32mPR #{pr_number} merged successfully.\033[0m")
    except subprocess.CalledProcessError as e:
        print(f"\033[31mFailed to merge PR #{pr_number}: {e}\033[0m")
        sys.exit(1)

    # Update manifest
    if task_id:
        task = find_task(manifest, task_id)
        if task:
            task["status"] = "completed"
            task["delivered_at"] = datetime.now(timezone.utc).isoformat()
            cfg.save_manifest(manifest)
            print(f"  Manifest updated: {task_id} -> completed")

    # Reputation update happens via GitHub Action on merge
    print("  Reputation tensor will be updated by CI on merge.")
