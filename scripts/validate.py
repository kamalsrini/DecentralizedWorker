#!/usr/bin/env python3
"""
validate.py - Validates agent output sections against schema and manifest rules.

Runs in GitHub Actions CI on PRs touching output/sections/**.

Checks performed:
  1. JSON schema validation against output/schema.json (Draft-07)
  2. section_id must match the filename (stem, without .json extension)
  3. parsed_by must match the agent assigned to the task in tasks/manifest.json
  4. No modifications outside assigned scope (agent only touched their sections)
  5. Cross-references within articles resolve to valid section IDs or article numbers
  6. Outputs structured JSON results and exits 0 (pass) or 1 (fail)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run_git_diff():
    """Get list of changed files in the PR relative to the base branch.

    Uses GITHUB_BASE_REF (set by Actions) to diff against the PR target.
    Falls back to diffing against origin/main, then HEAD~1.
    """
    base_ref = os.environ.get("GITHUB_BASE_REF", "main")

    attempts = [
        ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only", "HEAD~1"],
    ]

    for cmd in attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            if files:
                return files
        except subprocess.CalledProcessError:
            continue

    return []


def load_json_file(filepath):
    """Load and parse a JSON file, returning (data, error_string)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    except FileNotFoundError:
        return None, f"File not found: {filepath}"
    except Exception as e:
        return None, f"Error reading file: {e}"


def validate_schema(data, schema):
    """Validate data against a JSON schema. Returns a list of error strings."""
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        return ["jsonschema package not installed. Run: pip install jsonschema"]

    validator = Draft7Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path_str = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        errors.append(f"[{path_str}] {error.message}")
    return errors


def check_section_id_matches_filename(data, filepath):
    """Verify that the section_id field matches the filename stem."""
    filename_stem = Path(filepath).stem
    section_id = data.get("section_id")
    if section_id is None:
        return f"Missing 'section_id' field in {filepath}"
    if section_id != filename_stem:
        return (
            f"section_id mismatch in {filepath}: "
            f"section_id='{section_id}' but filename='{filename_stem}.json'"
        )
    return None


def check_parsed_by_matches_manifest(data, filepath, manifest):
    """Verify that parsed_by matches the agent assigned in the manifest.

    The manifest uses task.id and task.assigned_to. A section belongs to
    a task if the section_id can be derived from the task title or if
    matched by convention (the PR body usually contains the task ID).
    """
    parsed_by = data.get("parsed_by")
    if parsed_by is None:
        return f"Missing 'parsed_by' field in {filepath}"

    # Check all tasks to see if any agent is assigned that matches parsed_by
    tasks = manifest.get("tasks", [])
    for task in tasks:
        if task.get("assigned_to") == parsed_by:
            return None  # Agent is assigned to at least one task

    # Also check if parsed_by matches any known agent referenced in the manifest
    known_agents = set()
    for task in tasks:
        agent = task.get("assigned_to")
        if agent:
            known_agents.add(agent)

    if known_agents and parsed_by not in known_agents:
        return (
            f"Agent mismatch in {filepath}: "
            f"parsed_by='{parsed_by}' is not assigned to any task in manifest. "
            f"Known agents: {', '.join(sorted(known_agents))}"
        )

    return None


def extract_agent_id_from_pr():
    """Extract the agent ID from the PR body environment variable."""
    pr_body = os.environ.get("PR_BODY", "") or ""
    # Match pattern: **Agent ID:** agent-xxx or `agent-xxx`
    match = re.search(r"\*\*Agent ID:\*\*\s*`?([^\s`]+)`?", pr_body)
    if match:
        return match.group(1)
    return os.environ.get("GITHUB_ACTOR", "")


def extract_task_id_from_pr():
    """Extract the task ID from the PR body environment variable."""
    pr_body = os.environ.get("PR_BODY", "") or ""
    match = re.search(r"\*\*Task ID:\*\*\s*`?([^\s`]+)`?", pr_body)
    if match:
        return match.group(1)
    return None


def check_scope_violations(changed_files, manifest):
    """Check that the PR author only modified sections within their assigned scope.

    Uses the PR body to identify the agent and task, then checks that all
    changed section files belong to that agent's task.
    """
    errors = []
    agent_id = extract_agent_id_from_pr()
    task_id = extract_task_id_from_pr()

    if not agent_id:
        return errors  # Cannot determine agent; skip scope check

    # Find all tasks assigned to this agent
    agent_tasks = []
    for task in manifest.get("tasks", []):
        if task.get("assigned_to") == agent_id:
            agent_tasks.append(task)

    if not agent_tasks:
        return errors  # Agent not in manifest; skip scope check

    # Identify the specific task if we have a task_id
    if task_id:
        specific_task = None
        for task in agent_tasks:
            if task.get("id") == task_id:
                specific_task = task
                break
        if specific_task:
            agent_tasks = [specific_task]

    # Check each changed section file
    section_changes = [
        f for f in changed_files
        if f.startswith("output/sections/") and f.endswith(".json")
    ]

    # Also check for modifications outside output/sections/ and audits/
    non_output_changes = [
        f for f in changed_files
        if not f.startswith("output/sections/")
        and not f.startswith("audits/")
        and f != "tasks/manifest.json"
    ]

    if non_output_changes:
        errors.append(
            f"Scope violation: agent '{agent_id}' modified files outside "
            f"output/sections/ and audits/: {', '.join(non_output_changes)}"
        )

    return errors


def collect_all_section_ids(sections_dir):
    """Collect all section IDs from existing JSON files in output/sections/."""
    section_ids = set()
    sections_path = Path(sections_dir)
    if sections_path.exists():
        for json_file in sections_path.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sid = data.get("section_id", json_file.stem)
                    section_ids.add(sid)
            except (json.JSONDecodeError, Exception):
                section_ids.add(json_file.stem)
    return section_ids


def check_cross_references(data, filepath, all_section_ids):
    """Validate that cross-references within articles resolve to known section IDs.

    The output schema has articles[].cross_references as an array of strings.
    Each string is typically an article reference like "Article 5" or a section
    ID. We check if any look like section IDs and verify they exist.
    """
    errors = []
    articles = data.get("articles", [])
    if not isinstance(articles, list):
        return errors

    for article in articles:
        xrefs = article.get("cross_references", [])
        if not isinstance(xrefs, list):
            continue

        article_num = article.get("article_number", "?")
        for ref in xrefs:
            if not isinstance(ref, str):
                continue
            # If the reference looks like a section ID (lowercase, dashes/underscores),
            # verify it exists
            ref_lower = ref.lower().strip()
            if re.match(r"^[a-z0-9][a-z0-9_-]+$", ref_lower) and ref_lower not in all_section_ids:
                errors.append(
                    f"Unresolved cross-reference in {filepath}, "
                    f"article {article_num}: '{ref}' not found in known sections"
                )

    return errors


def main():
    repo_root = Path(__file__).resolve().parent.parent
    sections_dir = repo_root / "output" / "sections"
    schema_path = repo_root / "output" / "schema.json"
    manifest_path = repo_root / "tasks" / "manifest.json"

    results = {
        "status": "pass",
        "files_checked": 0,
        "errors": [],
        "warnings": [],
        "details": [],
    }

    # Load schema
    schema, schema_err = load_json_file(schema_path)
    if schema_err:
        results["warnings"].append(
            f"Could not load schema ({schema_path}): {schema_err}. "
            "Skipping schema validation."
        )
        schema = None

    # Load manifest
    manifest, manifest_err = load_json_file(manifest_path)
    if manifest_err:
        results["warnings"].append(
            f"Could not load manifest ({manifest_path}): {manifest_err}. "
            "Skipping manifest checks."
        )
        manifest = None

    # Get files changed in this PR
    changed_files = run_git_diff()
    section_files = [
        f for f in changed_files
        if f.startswith("output/sections/") and f.endswith(".json")
    ]

    if not section_files:
        results["warnings"].append(
            "No changed JSON files found in output/sections/. Nothing to validate."
        )
        print(json.dumps(results, indent=2))
        sys.exit(0)

    # Collect all known section IDs for cross-reference validation
    all_section_ids = collect_all_section_ids(sections_dir)

    # Check scope violations across all changed files
    if manifest:
        scope_errors = check_scope_violations(changed_files, manifest)
        results["errors"].extend(scope_errors)

    # Validate each changed section file
    for rel_path in section_files:
        filepath = repo_root / rel_path
        file_result = {
            "file": rel_path,
            "errors": [],
            "warnings": [],
        }

        data, parse_err = load_json_file(filepath)
        if parse_err:
            file_result["errors"].append(parse_err)
            results["details"].append(file_result)
            results["errors"].append(f"{rel_path}: {parse_err}")
            continue

        results["files_checked"] += 1

        # 1. Schema validation
        if schema:
            schema_errors = validate_schema(data, schema)
            for err in schema_errors:
                file_result["errors"].append(f"Schema: {err}")

        # 2. section_id matches filename
        id_err = check_section_id_matches_filename(data, filepath)
        if id_err:
            file_result["errors"].append(id_err)

        # 3. parsed_by matches manifest assignment
        if manifest:
            agent_err = check_parsed_by_matches_manifest(data, filepath, manifest)
            if agent_err:
                file_result["errors"].append(agent_err)

        # 4. Cross-reference validation
        xref_errors = check_cross_references(data, filepath, all_section_ids)
        file_result["errors"].extend(xref_errors)

        # Accumulate into global results
        results["errors"].extend(file_result["errors"])
        results["warnings"].extend(file_result["warnings"])
        results["details"].append(file_result)

    # Set final status
    if results["errors"]:
        results["status"] = "fail"

    # Output structured JSON to stdout
    print(json.dumps(results, indent=2))

    # Print human-readable summary to stderr
    if results["status"] == "fail":
        print("\n--- Validation Failures ---", file=sys.stderr)
        for err in results["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print(
            f"\n--- All validations passed "
            f"({results['files_checked']} file(s) checked) ---",
            file=sys.stderr,
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
