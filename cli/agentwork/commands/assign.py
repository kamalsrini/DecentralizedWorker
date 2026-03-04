"""agentwork assign <task-id> <agent-id> — Assign a task to an agent."""

import subprocess
import sys
from datetime import datetime, timezone

from agentwork.config import Config
from agentwork.manifest import find_task


def run(args):
    cfg = Config()
    manifest = cfg.load_manifest()
    task = find_task(manifest, args.task_id)

    if task is None:
        print(f"\033[31mTask '{args.task_id}' not found in manifest.\033[0m")
        sys.exit(1)

    if task.get("status") not in ("unassigned", None):
        current = task.get("assigned_to", "unknown")
        print(f"\033[33mTask '{args.task_id}' is already {task['status']} (assigned to {current}).\033[0m")
        sys.exit(1)

    # Update manifest
    task["assigned_to"] = args.agent_id
    task["status"] = "assigned"
    task["assigned_at"] = datetime.now(timezone.utc).isoformat()

    # Create GitHub Issue
    title = f"[TASK] {args.task_id}: {task.get('title', '')}"
    body = (
        f"## Task Assignment\n\n"
        f"**Task ID:** `{args.task_id}`\n"
        f"**Assigned To:** `{args.agent_id}`\n"
        f"**Deadline:** {task.get('deadline', 'N/A')}\n\n"
        f"### Description\n\n{task.get('title', '')}\n\n"
        f"### Output Requirements\n\n"
        f"- Output must conform to `output/schema.json`\n"
        f"- Place output files in `output/sections/`\n"
    )

    try:
        result = subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body, "--label", "task"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            issue_url = result.stdout.strip()
            # Extract issue number from URL
            issue_num = issue_url.rstrip("/").split("/")[-1]
            task["github_issue"] = int(issue_num) if issue_num.isdigit() else issue_url
            print(f"  Created issue: {issue_url}")
        else:
            # Try without label
            result = subprocess.run(
                ["gh", "issue", "create", "--title", title, "--body", body],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                issue_url = result.stdout.strip()
                issue_num = issue_url.rstrip("/").split("/")[-1]
                task["github_issue"] = int(issue_num) if issue_num.isdigit() else issue_url
                print(f"  Created issue: {issue_url}")
            else:
                print(f"\033[33m  Warning: Could not create GitHub issue: {result.stderr.strip()}\033[0m")
    except FileNotFoundError:
        print("\033[33m  Warning: 'gh' CLI not found. Skipping issue creation.\033[0m")

    cfg.save_manifest(manifest)

    print(f"\n\033[32mAssigned {args.task_id} to {args.agent_id}\033[0m")
    print(f"  Status: assigned")
    print(f"  Deadline: {task.get('deadline', 'N/A')}")
