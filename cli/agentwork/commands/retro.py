"""agentwork retro <task-id> — Show post-mortem for a completed task."""

import json
import sys

from agentwork.config import Config


def run(args):
    cfg = Config()
    retros_dir = cfg.retros_dir
    task_id = args.task_id

    # Find retro file matching task_id
    found = None
    if retros_dir.exists():
        for f in retros_dir.glob("*.json"):
            if task_id in f.stem:
                found = f
                break

    if not found:
        print(f"\033[33mNo retro found for task '{task_id}'.\033[0m")
        print(f"  Searched in: {retros_dir}")
        sys.exit(1)

    with open(found, "r") as f:
        retro = json.load(f)

    data = retro.get("retro", {})
    print(f"\n\033[1mPost-Mortem: {task_id}\033[0m")
    print(f"  Agent: {retro.get('agent_id', '?')}")
    print(f"  Project: {retro.get('project_id', '?')}")
    print()

    print(f"  \033[1mApproach:\033[0m")
    print(f"    {data.get('approach', 'N/A')}")
    print()

    print(f"  \033[1mChallenges:\033[0m")
    for c in data.get("challenges", []):
        print(f"    - {c}")
    print()

    print(f"  \033[1mSuggestions:\033[0m")
    for s in data.get("suggestions", []):
        print(f"    - {s}")
    print()

    print(f"  Tokens used: {data.get('time_spent_tokens', '?')}")
    print(f"  Self-assessment: {data.get('self_quality_assessment', '?')}")
    print()
