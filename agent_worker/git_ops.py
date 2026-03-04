from __future__ import annotations

"""Git operations helper for the agent worker runtime.

Provides functions for cloning repositories, creating branches, committing,
pushing, opening pull requests, and checking out PR branches. All git
operations shell out to the git and gh CLIs.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _run(cmd: list[str], cwd: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command with logging and error handling.

    Args:
        cmd: Command and arguments to run.
        cwd: Working directory for the command.
        check: Whether to raise on non-zero exit.

    Returns:
        The CompletedProcess result.

    Raises:
        subprocess.CalledProcessError: If check is True and the command fails.
    """
    logger.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    if result.stdout.strip():
        logger.debug("stdout: %s", result.stdout.strip())
    if result.stderr.strip():
        logger.debug("stderr: %s", result.stderr.strip())
    return result


def clone_repo(url: str, token: str, dest: str) -> str:
    """Clone a repository or pull latest if it already exists.

    The token is injected into the HTTPS URL for authentication.

    Args:
        url: The repository HTTPS URL (e.g. https://github.com/org/repo).
        token: GitHub personal access token for authentication.
        dest: Destination directory path.

    Returns:
        The absolute path to the cloned repository.
    """
    dest_path = Path(dest).resolve()

    if url.startswith("https://"):
        auth_url = url.replace("https://", f"https://x-access-token:{token}@")
    else:
        auth_url = url

    if (dest_path / ".git").is_dir():
        logger.info("Repository already exists at %s, pulling latest.", dest_path)
        _run(["git", "fetch", "--all"], cwd=str(dest_path))
        _run(["git", "checkout", "main"], cwd=str(dest_path), check=False)
        _run(["git", "pull", "--ff-only"], cwd=str(dest_path), check=False)
    else:
        logger.info("Cloning repository from %s to %s", url, dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", auth_url, str(dest_path)])

    # Configure git identity for commits
    _run(["git", "config", "user.name", "agentwork-bot"], cwd=str(dest_path))
    _run(["git", "config", "user.email", "agentwork-bot@users.noreply.github.com"], cwd=str(dest_path))

    return str(dest_path)


def create_branch(branch_name: str, repo_dir: str) -> None:
    """Create and checkout a new branch from the current HEAD.

    If the branch already exists locally, it is checked out and reset to match
    the remote main branch.

    Args:
        branch_name: The branch name to create.
        repo_dir: Path to the git repository.
    """
    logger.info("Creating branch: %s", branch_name)

    # Check if branch already exists
    result = _run(
        ["git", "branch", "--list", branch_name],
        cwd=repo_dir,
        check=False,
    )
    if result.stdout.strip():
        logger.info("Branch %s already exists, checking out.", branch_name)
        _run(["git", "checkout", branch_name], cwd=repo_dir)
    else:
        _run(["git", "checkout", "-b", branch_name], cwd=repo_dir)


def commit_and_push(message: str, files: list[str], repo_dir: str) -> None:
    """Stage files, commit with the given message, and push to the remote.

    Args:
        message: Commit message.
        files: List of file paths (relative to repo_dir) to stage.
        repo_dir: Path to the git repository.

    Raises:
        subprocess.CalledProcessError: If any git command fails.
    """
    logger.info("Committing %d file(s) with message: %s", len(files), message[:80])

    for filepath in files:
        _run(["git", "add", filepath], cwd=repo_dir)

    _run(["git", "commit", "-m", message], cwd=repo_dir)

    # Get current branch name
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    branch = result.stdout.strip()

    _run(["git", "push", "-u", "origin", branch], cwd=repo_dir)
    logger.info("Pushed branch %s to origin.", branch)


def open_pr(title: str, body: str, base: str, head: str, repo_dir: str) -> str:
    """Open a pull request using the GitHub CLI.

    Args:
        title: PR title.
        body: PR body in markdown.
        base: Base branch (e.g. 'main').
        head: Head branch (the feature branch).
        repo_dir: Path to the git repository.

    Returns:
        The URL of the created pull request.

    Raises:
        subprocess.CalledProcessError: If the gh command fails.
    """
    logger.info("Opening PR: %s (%s -> %s)", title, head, base)
    result = _run(
        [
            "gh", "pr", "create",
            "--title", title,
            "--body", body,
            "--base", base,
            "--head", head,
        ],
        cwd=repo_dir,
    )
    pr_url = result.stdout.strip()
    logger.info("Pull request created: %s", pr_url)
    return pr_url


def checkout_pr(pr_number: int, repo_dir: str) -> dict:
    """Checkout a pull request branch and return metadata about it.

    Uses `gh pr checkout` to fetch and checkout the PR branch, then extracts
    metadata from `gh pr view`.

    Args:
        pr_number: The pull request number.
        repo_dir: Path to the git repository.

    Returns:
        A dict with keys: pr_number, head_branch, author, title, body.

    Raises:
        subprocess.CalledProcessError: If the gh command fails.
    """
    logger.info("Checking out PR #%d", pr_number)
    _run(["gh", "pr", "checkout", str(pr_number)], cwd=repo_dir)

    # Gather PR metadata
    fields = "number,headRefName,author,title,body"
    result = _run(
        ["gh", "pr", "view", str(pr_number), "--json", fields],
        cwd=repo_dir,
    )

    import json
    pr_data = json.loads(result.stdout)

    metadata = {
        "pr_number": pr_data.get("number", pr_number),
        "head_branch": pr_data.get("headRefName", ""),
        "author": pr_data.get("author", {}).get("login", "unknown"),
        "title": pr_data.get("title", ""),
        "body": pr_data.get("body", ""),
    }
    logger.info("Checked out PR #%d, branch: %s, author: %s", pr_number, metadata["head_branch"], metadata["author"])
    return metadata


def add_pr_comment(pr_number: int, body: str, repo_dir: str) -> None:
    """Add a comment to a pull request.

    Args:
        pr_number: The pull request number.
        body: The comment body in markdown.
        repo_dir: Path to the git repository.
    """
    logger.info("Adding comment to PR #%d", pr_number)
    _run(
        ["gh", "pr", "comment", str(pr_number), "--body", body],
        cwd=repo_dir,
    )


def get_changed_files(repo_dir: str, base: str = "main") -> list[str]:
    """Get list of files changed between the current branch and base.

    Args:
        repo_dir: Path to the git repository.
        base: The base branch to diff against.

    Returns:
        A list of changed file paths relative to the repo root.
    """
    result = _run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=repo_dir,
        check=False,
    )
    files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    return files
