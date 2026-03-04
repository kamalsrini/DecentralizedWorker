#!/usr/bin/env python3
"""
update_reputation.py - Computes tensor updates and maintains the reputation ledger.

Runs on PR merge (pull_request closed + merged) for PRs touching
output/sections/** or audits/**.

Tensor dimensions:
  - technical_accuracy: from CI validation pass/fail
  - collaborative_signal: count review iterations (changes_requested reviews)
  - reliability: compare delivered_at vs deadline from manifest
  - audit_contribution: from audit-quality:N label on audit PRs

Decay formula:
  R_next = (R_prev * 0.9) + (S_task * 0.1) - sum(penalties)
  Where S_task = (technical_accuracy * 0.4) + (collaborative_signal * 0.25)
               + (reliability * 0.25) + (audit_contribution * 0.1)

Anomaly penalties:
  - scope_creep: 0.05
  - schema_violation: 0.10
  - suspicious_pattern: 0.15

The ledger structure conforms to reputation/schema.json, with each tensor
dimension containing scores history, a current value, and a computed_by label.

Environment variables (set by the GitHub Actions workflow):
  GITHUB_REPOSITORY, GITHUB_EVENT_PATH, PR_NUMBER, PR_AUTHOR,
  PR_BODY, PR_MERGED_AT, PR_HEAD_REF
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# --- Constants ---

WEIGHT_TECHNICAL = 0.4
WEIGHT_COLLABORATIVE = 0.25
WEIGHT_RELIABILITY = 0.25
WEIGHT_AUDIT = 0.1

DECAY = 0.9
LEARNING_RATE = 0.1

PENALTY_VALUES = {
    "scope_creep": 0.05,
    "schema_violation": 0.10,
    "suspicious_pattern": 0.15,
}

DEFAULT_COMPOSITE = 0.5
DEFAULT_DIMENSION = 0.5


# --- Utilities ---

def run_gh(args, check=False):
    """Run a gh CLI command and return stdout (empty string on failure)."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            check=check,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def load_json_file(filepath):
    """Load a JSON file; returns empty dict on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json_file(filepath, data):
    """Save data to a JSON file, creating parent dirs if needed."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# --- PR Info Extraction ---

def get_pr_info():
    """Collect PR metadata from environment."""
    return {
        "number": os.environ.get("PR_NUMBER", ""),
        "author": os.environ.get("PR_AUTHOR", os.environ.get("GITHUB_ACTOR", "")),
        "body": os.environ.get("PR_BODY", "") or "",
        "merged_at": os.environ.get("PR_MERGED_AT", ""),
        "head_ref": os.environ.get("PR_HEAD_REF", ""),
    }


def extract_agent_and_task(pr_info, manifest):
    """Determine agent_id and task_id from the PR context.

    Checks PR body for **Agent ID:** and **Task ID:** patterns, then
    falls back to branch name matching and manifest assignment lookup.
    """
    agent_id = pr_info["author"]
    task_id = None
    body = pr_info.get("body", "") or ""
    head_ref = pr_info.get("head_ref", "") or ""

    # Extract from PR body template
    agent_match = re.search(r"\*\*Agent ID:\*\*\s*`?([^\s`]+)`?", body)
    if agent_match:
        agent_id = agent_match.group(1)

    task_match = re.search(r"\*\*Task ID:\*\*\s*`?([^\s`]+)`?", body)
    if task_match:
        task_id = task_match.group(1)

    # Fallback: match task ID in branch name
    if not task_id:
        for task in manifest.get("tasks", []):
            tid = task.get("id", "")
            if tid and tid in head_ref:
                task_id = tid
                break

    # Fallback: find by assigned agent
    if not task_id:
        for task in manifest.get("tasks", []):
            if task.get("assigned_to") == agent_id:
                task_id = task.get("id")
                break

    return agent_id, task_id


# --- Tensor Dimension Computation ---

def compute_technical_accuracy(repo, pr_number):
    """Check CI check-run results for this PR. Returns score 0.0-1.0.

    Queries the PR's status checks via gh. All checks passing = 1.0,
    proportional otherwise.
    """
    result = run_gh([
        "pr", "checks", str(pr_number),
        "--repo", repo,
        "--json", "state",
        "--jq", ".[].state",
    ])

    if not result:
        return 1.0  # No checks configured = assume pass

    states = [s.strip().upper() for s in result.split("\n") if s.strip()]
    total = len(states)
    if total == 0:
        return 1.0

    passed = sum(1 for s in states if s in ("SUCCESS", "PASS", "COMPLETED"))
    return round(passed / total, 4)


def compute_collaborative_signal(repo, pr_number):
    """Score based on number of CHANGES_REQUESTED reviews. Returns 0.0-1.0.

    0 revisions = 1.0, 1 = 0.8, 2 = 0.6, 3+ = 0.4 (clamped).
    """
    result = run_gh([
        "api", f"repos/{repo}/pulls/{pr_number}/reviews",
        "--jq", '[.[] | select(.state == "CHANGES_REQUESTED")] | length',
    ])

    if not result or not result.strip().isdigit():
        return 1.0

    changes_requested = int(result.strip())
    score = max(0.4, 1.0 - (changes_requested * 0.2))
    return round(score, 4)


def compute_reliability(manifest, task_id, merged_at_str):
    """Score based on delivery timeliness. Returns 0.0-1.0.

    On time / early = 1.0. Late penalties: 1 day = 0.8, 2 = 0.6, 3+ = 0.4.
    """
    if not task_id or not merged_at_str:
        return 1.0

    task = None
    for t in manifest.get("tasks", []):
        if t.get("id") == task_id:
            task = t
            break

    if not task:
        return 1.0

    deadline_str = task.get("deadline")
    if not deadline_str:
        return 1.0

    try:
        merged_at = _parse_iso(merged_at_str)
        deadline = _parse_iso(deadline_str)

        delta_days = (merged_at - deadline).total_seconds() / 86400.0
        if delta_days <= 0:
            return 1.0
        elif delta_days <= 1:
            return 0.8
        elif delta_days <= 2:
            return 0.6
        else:
            return 0.4
    except (ValueError, TypeError):
        return 1.0


def _parse_iso(s):
    """Parse an ISO-8601 datetime string, handling trailing Z."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_audit_contribution(repo, pr_number):
    """Read audit-quality:N label for audit PRs. Returns 0.0-1.0.

    If the label audit-quality:N is present, scale N (1-5) to 0.0-1.0.
    Default 0.5 if no label found.
    """
    result = run_gh([
        "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "labels",
        "--jq", ".labels[].name",
    ])

    if not result:
        return 0.5

    for label in result.split("\n"):
        label = label.strip()
        if label.startswith("audit-quality:"):
            try:
                quality = int(label.split(":")[1])
                return round(min(1.0, max(0.0, quality / 5.0)), 4)
            except (ValueError, IndexError):
                pass

    return 0.5


# --- Anomaly Detection ---

def detect_anomalies(repo, pr_number):
    """Detect anomaly conditions. Returns dict of {anomaly_type: bool}."""
    anomalies = {}

    # Scope creep: changed files outside output/sections/ and audits/
    diff_result = run_gh([
        "pr", "diff", str(pr_number),
        "--repo", repo,
        "--name-only",
    ])
    if diff_result:
        files = [f.strip() for f in diff_result.split("\n") if f.strip()]
        non_output = [
            f for f in files
            if not f.startswith("output/sections/")
            and not f.startswith("audits/")
            and f != "tasks/manifest.json"
        ]
        anomalies["scope_creep"] = len(non_output) > 0
    else:
        anomalies["scope_creep"] = False

    # Schema violation: check PR comments for validation failure marker
    comments_result = run_gh([
        "api", f"repos/{repo}/issues/{pr_number}/comments",
        "--jq", ".[].body",
    ])
    anomalies["schema_violation"] = bool(
        comments_result and '"status": "fail"' in comments_result
    )

    # Suspicious pattern: excessively large PR
    size_result = run_gh([
        "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "additions,deletions",
        "--jq", ".additions + .deletions",
    ])
    if size_result and size_result.strip().isdigit():
        anomalies["suspicious_pattern"] = int(size_result.strip()) > 5000
    else:
        anomalies["suspicious_pattern"] = False

    return anomalies


# --- Score & Reputation Computation ---

def compute_s_task(technical, collaborative, reliability, audit):
    """Compute the weighted task score S_task."""
    return (
        technical * WEIGHT_TECHNICAL
        + collaborative * WEIGHT_COLLABORATIVE
        + reliability * WEIGHT_RELIABILITY
        + audit * WEIGHT_AUDIT
    )


def compute_penalties(anomalies, task_id):
    """Sum up penalties for flagged anomalies. Returns (total, list_of_dicts)."""
    total = 0.0
    active = []
    now = datetime.now(timezone.utc).isoformat()
    for anomaly_type, detected in anomalies.items():
        if detected:
            penalty = PENALTY_VALUES.get(anomaly_type, 0.0)
            total += penalty
            active.append({
                "type": anomaly_type,
                "penalty": penalty,
                "task_id": task_id or "unknown",
                "flagged_at": now,
            })
    return total, active


def ensure_agent_entry(ledger, agent_id):
    """Ensure the agent has a properly structured entry in the ledger."""
    agents = ledger.setdefault("agents", {})
    if agent_id not in agents:
        agents[agent_id] = _new_agent_entry()
    else:
        # Ensure all required keys exist
        entry = agents[agent_id]
        defaults = _new_agent_entry()
        for key, val in defaults.items():
            if key not in entry:
                entry[key] = val
        # Ensure tensor dimensions exist
        for dim in ("technical_accuracy", "collaborative_signal", "reliability", "audit_contribution"):
            if dim not in entry.get("tensor", {}):
                entry["tensor"][dim] = defaults["tensor"][dim]


def _new_agent_entry():
    """Create a fresh agent ledger entry conforming to reputation/schema.json."""
    dim_template = lambda desc, comp: {
        "description": desc,
        "scores": [],
        "current": DEFAULT_DIMENSION,
        "computed_by": comp,
    }
    return {
        "tensor": {
            "technical_accuracy": dim_template(
                "CI validation pass rate", "automated"
            ),
            "collaborative_signal": dim_template(
                "Review iteration efficiency", "automated"
            ),
            "reliability": dim_template(
                "On-time delivery rate", "automated"
            ),
            "audit_contribution": dim_template(
                "Quality of audit reviews", "peer_audit"
            ),
        },
        "composite_R": DEFAULT_COMPOSITE,
        "anomaly_flags": [],
        "tasks_completed": 0,
        "tasks_assigned": 0,
        "audits_performed": 0,
        "last_active": None,
    }


def update_agent_reputation(ledger, agent_id, tensor_scores, s_task, penalty_total, active_penalties, task_id, is_audit_pr):
    """Apply the decay formula and update the ledger for the given agent.

    Updates each tensor dimension's scores list and current value using
    exponential moving average, then computes composite_R.
    """
    ensure_agent_entry(ledger, agent_id)
    agent = ledger["agents"][agent_id]
    now = datetime.now(timezone.utc).isoformat()

    # Update each tensor dimension
    for dim_name, new_score in tensor_scores.items():
        dim = agent["tensor"][dim_name]
        dim["scores"].append(round(new_score, 4))
        old_current = dim["current"]
        dim["current"] = round(
            (old_current * DECAY) + (new_score * LEARNING_RATE), 4
        )

    # Compute composite_R
    r_prev = agent["composite_R"]
    r_next = (r_prev * DECAY) + (s_task * LEARNING_RATE) - penalty_total
    r_next = round(max(0.0, min(1.0, r_next)), 4)
    agent["composite_R"] = r_next

    # Record anomaly flags
    agent["anomaly_flags"].extend(active_penalties)

    # Increment counters
    if is_audit_pr:
        agent["audits_performed"] += 1
    else:
        agent["tasks_completed"] += 1

    agent["last_active"] = now

    # Update ledger metadata
    ledger["last_updated"] = now

    return ledger


# --- Main ---

def main():
    repo_root = Path(__file__).resolve().parent.parent
    ledger_path = repo_root / "reputation" / "ledger.json"
    manifest_path = repo_root / "tasks" / "manifest.json"
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    pr_info = get_pr_info()
    manifest = load_json_file(manifest_path)
    ledger = load_json_file(ledger_path)

    # Ensure the ledger has the required top-level keys
    if "version" not in ledger:
        ledger["version"] = "1.0.0"
    if "agents" not in ledger:
        ledger["agents"] = {}

    pr_number = pr_info["number"]
    if not pr_number:
        print("ERROR: PR_NUMBER not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Processing reputation update for PR #{pr_number}", file=sys.stderr)

    # Identify agent and task
    agent_id, task_id = extract_agent_and_task(pr_info, manifest)
    print(f"Agent: {agent_id}, Task: {task_id}", file=sys.stderr)

    if not agent_id:
        print("ERROR: Could not determine agent_id.", file=sys.stderr)
        sys.exit(1)

    # Determine if this is an audit PR (touches audits/ directory)
    head_ref = pr_info.get("head_ref", "") or ""
    body = pr_info.get("body", "") or ""
    is_audit_pr = "audit" in head_ref.lower() or "audit" in body.lower()

    # Compute tensor dimension scores
    print("Computing technical_accuracy...", file=sys.stderr)
    technical = compute_technical_accuracy(repo, pr_number)

    print("Computing collaborative_signal...", file=sys.stderr)
    collaborative = compute_collaborative_signal(repo, pr_number)

    print("Computing reliability...", file=sys.stderr)
    reliability = compute_reliability(manifest, task_id, pr_info.get("merged_at", ""))

    print("Computing audit_contribution...", file=sys.stderr)
    audit = compute_audit_contribution(repo, pr_number)

    tensor_scores = {
        "technical_accuracy": technical,
        "collaborative_signal": collaborative,
        "reliability": reliability,
        "audit_contribution": audit,
    }
    print(f"Tensor scores: {json.dumps(tensor_scores)}", file=sys.stderr)

    # Compute S_task
    s_task = compute_s_task(technical, collaborative, reliability, audit)
    print(f"S_task: {s_task:.4f}", file=sys.stderr)

    # Detect anomalies
    print("Detecting anomalies...", file=sys.stderr)
    anomalies = detect_anomalies(repo, pr_number)
    penalty_total, active_penalties = compute_penalties(anomalies, task_id)
    if active_penalties:
        print(f"Anomalies detected: {json.dumps(active_penalties)}", file=sys.stderr)
    else:
        print("No anomalies detected.", file=sys.stderr)

    # Update the ledger
    ledger = update_agent_reputation(
        ledger, agent_id, tensor_scores, s_task,
        penalty_total, active_penalties, task_id, is_audit_pr,
    )

    agent_data = ledger["agents"][agent_id]
    print(
        f"Reputation updated: composite_R = {agent_data['composite_R']}",
        file=sys.stderr,
    )

    # Persist
    save_json_file(ledger_path, ledger)
    print(f"Ledger saved to {ledger_path}", file=sys.stderr)

    # Output summary to stdout
    summary = {
        "agent_id": agent_id,
        "task_id": task_id,
        "pr_number": pr_number,
        "is_audit_pr": is_audit_pr,
        "tensor_scores": {k: round(v, 4) for k, v in tensor_scores.items()},
        "s_task": round(s_task, 4),
        "penalties": active_penalties,
        "composite_R": agent_data["composite_R"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
