"""agentwork audit <pr-number> <auditor-agent-id> — Assign a peer auditor."""

import json
import subprocess
import sys

from agentwork.config import Config
from agentwork.manifest import find_task


def run(args):
    cfg = Config()
    manifest = cfg.load_manifest()
    pr_number = args.pr_number
    auditor_id = args.auditor_id

    # Get PR info
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title,body,author,headRefName"],
            capture_output=True, text=True, check=True,
        )
        pr_data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"\033[31mFailed to get PR #{pr_number}: {e}\033[0m")
        sys.exit(1)

    pr_author = pr_data.get("author", {}).get("login", "unknown")

    if auditor_id == pr_author:
        print(f"\033[31mAuditor cannot be the same as the PR author ({pr_author}).\033[0m")
        sys.exit(1)

    # Find task from PR body or branch
    task_id = None
    body = pr_data.get("body", "") or ""
    head_ref = pr_data.get("headRefName", "")

    for line in body.split("\n"):
        if "task" in line.lower() and "id" in line.lower():
            parts = line.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip().strip("`").strip('"')
                if candidate:
                    task_id = candidate
                    break

    if not task_id:
        for task in manifest.get("tasks", []):
            tid = task.get("id", "")
            if tid and tid in head_ref:
                task_id = tid
                break

    # Update manifest auditor field
    if task_id:
        task = find_task(manifest, task_id)
        if task:
            task["auditor"] = auditor_id
            cfg.save_manifest(manifest)

    # Create audit issue
    issue_title = f"[AUDIT] {auditor_id} to review PR #{pr_number} (task {task_id or 'unknown'})"
    issue_body = (
        f"## Audit Assignment\n\n"
        f"**Auditor:** `{auditor_id}`\n"
        f"**PR:** #{pr_number}\n"
        f"**Task:** `{task_id or 'unknown'}`\n"
        f"**Original Author:** `{pr_author}`\n\n"
        f"### Checklist\n\n"
        f"- [ ] Schema compliance\n"
        f"- [ ] Factual accuracy\n"
        f"- [ ] Cross-reference integrity\n"
        f"- [ ] Scope adherence\n"
    )

    try:
        result = subprocess.run(
            ["gh", "issue", "create", "--title", issue_title, "--body", issue_body],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            print(f"  Audit issue created: {result.stdout.strip()}")
    except FileNotFoundError:
        print("\033[33m  Warning: 'gh' CLI not found.\033[0m")

    # Comment on PR
    comment = (
        f"## Auditor Assigned\n\n"
        f"**Auditor:** `{auditor_id}`\n"
        f"**Task:** `{task_id or 'unknown'}`\n\n"
        f"Please wait for the peer audit before merging."
    )
    try:
        subprocess.run(
            ["gh", "pr", "comment", str(pr_number), "--body", comment],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        pass

    print(f"\n\033[32mAssigned {auditor_id} as auditor for PR #{pr_number}\033[0m")
