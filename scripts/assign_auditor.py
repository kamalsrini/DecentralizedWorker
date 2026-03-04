#!/usr/bin/env python3
"""
assign_auditor.py - Assigns a peer auditor for a PR via round-robin.

Reads tasks/manifest.json to find registered agents, selects one who is
not the PR author via round-robin, creates an audit issue on GitHub, and
comments on the PR with the assignment details.

Environment variables (set by the GitHub Actions workflow):
  GITHUB_REPOSITORY  - owner/repo
  GITHUB_TOKEN       - auth token for gh CLI
  PR_NUMBER          - pull request number
  PR_AUTHOR          - PR author login
  PR_TITLE           - PR title
  PR_BODY            - PR body markdown
  PR_HEAD_REF        - head branch name
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run_command(cmd, check=True):
    """Run a shell command and return stdout. Raises on failure if check=True."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    return result.stdout.strip()


def load_json_file(filepath):
    """Load a JSON file and return its contents."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(filepath, data):
    """Write data to a JSON file with trailing newline."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def get_pr_info():
    """Collect PR metadata from environment variables."""
    return {
        "number": os.environ.get("PR_NUMBER", ""),
        "author": os.environ.get("PR_AUTHOR", os.environ.get("GITHUB_ACTOR", "")),
        "title": os.environ.get("PR_TITLE", ""),
        "body": os.environ.get("PR_BODY", "") or "",
        "head_ref": os.environ.get("PR_HEAD_REF", ""),
    }


def find_task_for_pr(manifest, pr_info):
    """Match the PR to a task in the manifest.

    Strategy order:
      1. Extract task ID from PR body (**Task ID:** pattern)
      2. Match task ID in the branch name
      3. Match by assigned_to == PR author
    """
    tasks = manifest.get("tasks", [])
    head_ref = pr_info.get("head_ref", "")
    pr_body = pr_info.get("body", "") or ""
    pr_author = pr_info.get("author", "")

    # Strategy 1: task ID in PR body
    match = re.search(r"\*\*Task ID:\*\*\s*`?([^\s`]+)`?", pr_body)
    if match:
        target_id = match.group(1)
        for task in tasks:
            if task.get("id") == target_id:
                return task

    # Strategy 2: task ID in branch name (e.g., task/task-001 or agent/task-003)
    for task in tasks:
        task_id = task.get("id", "")
        if task_id and task_id in head_ref:
            return task

    # Strategy 3: assigned agent matches PR author
    for task in tasks:
        if task.get("assigned_to") == pr_author:
            return task

    return tasks[0] if tasks else None


def get_all_agents(manifest):
    """Derive the full agent list from all assigned_to values in tasks."""
    agents = set()
    for task in manifest.get("tasks", []):
        agent = task.get("assigned_to")
        if agent:
            agents.add(agent)
    return sorted(agents)


def get_round_robin_state(manifest):
    """Read the current round-robin index from manifest _meta."""
    meta = manifest.get("_meta", {})
    return meta.get("auditor_rr_index", 0)


def set_round_robin_state(manifest, index):
    """Write the round-robin index into manifest _meta."""
    if "_meta" not in manifest:
        manifest["_meta"] = {}
    manifest["_meta"]["auditor_rr_index"] = index


def select_auditor(manifest, pr_author):
    """Select an auditor via round-robin, excluding the PR author."""
    agents = get_all_agents(manifest)
    eligible = [a for a in agents if a != pr_author]

    if not eligible:
        print(
            f"WARNING: No eligible auditors found (excluding '{pr_author}'). "
            "Falling back to full agent list.",
            file=sys.stderr,
        )
        eligible = agents

    if not eligible:
        print("ERROR: No agents found in manifest tasks.", file=sys.stderr)
        sys.exit(1)

    rr_index = get_round_robin_state(manifest)
    selected_index = rr_index % len(eligible)
    auditor = eligible[selected_index]

    # Advance the round-robin pointer
    set_round_robin_state(manifest, rr_index + 1)
    return auditor


def create_audit_issue(repo, pr_info, task, auditor_id):
    """Create a GitHub issue for the audit assignment using gh CLI.

    Uses the audit-assignment issue template fields in the body for
    structured downstream consumption.
    """
    task_id = task.get("id", "unknown") if task else "unknown"
    task_title = task.get("title", "") if task else ""
    pr_number = pr_info["number"]
    deadline = task.get("deadline", "") if task else ""

    title = f"[AUDIT] {auditor_id} to review PR #{pr_number} ({task_id})"

    body = (
        f"## Audit Assignment\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Audit ID** | audit-{task_id}-pr{pr_number} |\n"
        f"| **PR Number** | #{pr_number} |\n"
        f"| **Original Agent** | {pr_info['author']} |\n"
        f"| **Auditor** | {auditor_id} |\n"
        f"| **Task ID** | {task_id} |\n"
        f"| **Task** | {task_title} |\n"
        f"| **Deadline** | {deadline} |\n\n"
        f"### Audit Scope\n\n"
        f"- [ ] Schema compliance against `output/schema.json`\n"
        f"- [ ] Factual accuracy against source material\n"
        f"- [ ] Cross-reference integrity\n"
        f"- [ ] Completeness of parsed content\n"
        f"- [ ] Submit audit report to `audits/` directory\n\n"
        f"### Instructions\n\n"
        f"1. Check out PR #{pr_number} and review the output in `output/sections/`\n"
        f"2. Validate against the source material in `source/`\n"
        f"3. Create an audit report JSON conforming to `audits/schema.json`\n"
        f"4. Submit the audit as a PR and add label `audit-quality:N` (1-5)\n"
    )

    try:
        result = run_command([
            "gh", "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body", body,
            "--label", "audit,peer-review",
        ])
        print(f"Created audit issue: {result}", file=sys.stderr)
        return result
    except subprocess.CalledProcessError:
        # Labels may not exist yet; try without them
        try:
            result = run_command([
                "gh", "issue", "create",
                "--repo", repo,
                "--title", title,
                "--body", body,
            ])
            print(f"Created audit issue (no labels): {result}", file=sys.stderr)
            return result
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to create audit issue: {e.stderr}", file=sys.stderr)
            return None


def comment_on_pr(repo, pr_number, auditor_id, task, issue_url):
    """Post a comment on the PR announcing the auditor assignment."""
    task_id = task.get("id", "unknown") if task else "unknown"

    comment = (
        f"## Auditor Assigned\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Auditor** | `{auditor_id}` |\n"
        f"| **Task** | `{task_id}` |\n"
    )
    if issue_url:
        comment += f"| **Audit Issue** | {issue_url} |\n"
    comment += (
        f"\nSelected via round-robin from eligible agents. "
        f"Please wait for the audit review before merging.\n"
    )

    try:
        run_command([
            "gh", "pr", "comment", str(pr_number),
            "--repo", repo,
            "--body", comment,
        ])
        print(f"Posted auditor comment on PR #{pr_number}", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"WARNING: Failed to comment on PR: {e.stderr}", file=sys.stderr)


def update_manifest_with_auditor(manifest, manifest_path, task, auditor_id):
    """Set the auditor field on the matched task and persist to disk."""
    if task is None:
        return

    task_id = task.get("id")
    for t in manifest.get("tasks", []):
        if t.get("id") == task_id:
            t["auditor"] = auditor_id
            break

    save_json_file(manifest_path, manifest)
    print(f"Updated manifest: task {task_id} auditor = {auditor_id}", file=sys.stderr)

    # Commit and push the manifest update
    try:
        run_command(["git", "config", "user.name", "agentwork-bot"])
        run_command(["git", "config", "user.email", "agentwork-bot@users.noreply.github.com"])
        run_command(["git", "add", str(manifest_path)])
        run_command([
            "git", "commit", "-m",
            f"chore: assign auditor {auditor_id} to {task_id}",
        ])
        run_command(["git", "push"])
        print("Committed and pushed manifest update.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"WARNING: Could not commit manifest update: {e}", file=sys.stderr)


def main():
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "tasks" / "manifest.json"
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not manifest_path.exists():
        print(f"ERROR: Manifest not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    manifest = load_json_file(manifest_path)
    pr_info = get_pr_info()

    if not pr_info["number"]:
        print("ERROR: PR_NUMBER not set in environment.", file=sys.stderr)
        sys.exit(1)

    if not pr_info["author"]:
        print("ERROR: PR author not available in environment.", file=sys.stderr)
        sys.exit(1)

    print(f"PR #{pr_info['number']} by {pr_info['author']}", file=sys.stderr)

    # Match PR to a task
    task = find_task_for_pr(manifest, pr_info)
    if task:
        print(f"Matched task: {task.get('id', 'unknown')}", file=sys.stderr)
    else:
        print("WARNING: Could not match PR to a specific task.", file=sys.stderr)

    # Select auditor
    auditor_id = select_auditor(manifest, pr_info["author"])
    print(f"Selected auditor: {auditor_id}", file=sys.stderr)

    # Create audit issue
    issue_url = create_audit_issue(repo, pr_info, task, auditor_id)

    # Comment on PR
    comment_on_pr(repo, pr_info["number"], auditor_id, task, issue_url)

    # Update manifest
    update_manifest_with_auditor(manifest, manifest_path, task, auditor_id)

    print("Auditor assignment complete.", file=sys.stderr)


if __name__ == "__main__":
    main()
