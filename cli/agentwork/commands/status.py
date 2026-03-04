"""agentwork status — Show task board and project status."""

from agentwork.config import Config
from agentwork.manifest import get_task_stats


def _truncate(s: str, length: int) -> str:
    return s[:length - 1] + "\u2026" if len(s) > length else s


def run(args):
    cfg = Config()
    manifest = cfg.load_manifest()
    tasks = manifest.get("tasks", [])

    print(f"\n\033[1m{manifest.get('project', 'AgentWork Project')}\033[0m\n")

    # Table header
    hdr = (
        f"  {'ID':<12} {'Title':<40} {'Agent':<15} {'Status':<14} "
        f"{'Deadline':<12} {'Auditor':<12}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for t in tasks:
        tid = t.get("id", "?")
        title = _truncate(t.get("title", ""), 38)
        agent = t.get("assigned_to") or "\033[90m—\033[0m"
        status = t.get("status", "?")
        deadline = (t.get("deadline") or "")[:10]
        auditor = t.get("auditor") or "\033[90m—\033[0m"

        # Color status
        color = {
            "unassigned": "\033[90m",
            "assigned": "\033[33m",
            "in_progress": "\033[34m",
            "submitted": "\033[36m",
            "completed": "\033[32m",
        }.get(status, "")
        reset = "\033[0m" if color else ""

        print(
            f"  {tid:<12} {title:<40} {agent:<15} "
            f"{color}{status:<14}{reset} {deadline:<12} {auditor:<12}"
        )

    # Summary
    stats = get_task_stats(manifest)
    print()
    total = stats.get("total", 0)
    completed = stats.get("completed", 0)
    assigned = stats.get("assigned", 0) + stats.get("in_progress", 0)
    unassigned = stats.get("unassigned", 0)
    submitted = stats.get("submitted", 0)

    print(f"  Total: {total}  |  Unassigned: {unassigned}  |  "
          f"Active: {assigned}  |  Submitted: {submitted}  |  Completed: {completed}")
    print()
