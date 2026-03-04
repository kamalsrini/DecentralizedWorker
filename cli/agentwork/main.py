"""Main CLI entry point for AgentWork."""

import argparse
import sys

from agentwork import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentwork",
        description="AgentWork — Decentralized Agent Collaboration Platform CLI",
    )
    parser.add_argument("--version", action="version", version=f"agentwork {__version__}")

    sub = parser.add_subparsers(dest="command")

    # init
    init_p = sub.add_parser("init", help="Initialize or verify an AgentWork project")
    init_p.add_argument("--repo", help="GitHub repo URL to clone")

    # assign
    assign_p = sub.add_parser("assign", help="Assign a task to an agent")
    assign_p.add_argument("task_id", help="Task ID from manifest (e.g., task-001)")
    assign_p.add_argument("agent_id", help="Agent ID to assign (e.g., agent-alpha)")

    # status
    sub.add_parser("status", help="Show task board and project status")

    # accept
    accept_p = sub.add_parser("accept", help="Accept and merge a PR, update reputation")
    accept_p.add_argument("pr_number", type=int, help="PR number to merge")

    # audit
    audit_p = sub.add_parser("audit", help="Assign a peer auditor to a PR")
    audit_p.add_argument("pr_number", type=int, help="PR number to audit")
    audit_p.add_argument("auditor_id", help="Auditor agent ID")

    # agents
    sub.add_parser("agents", help="List registered agents and their reputation")

    # register
    reg_p = sub.add_parser("register", help="Register a new agent")
    reg_p.add_argument("agent_id", help="Unique agent identifier")
    reg_p.add_argument("--type", dest="agent_type", choices=["ai_agent", "human"],
                        default="ai_agent", help="Agent type (default: ai_agent)")
    reg_p.add_argument("--owner", default="", help="GitHub handle of agent owner")

    # retro
    retro_p = sub.add_parser("retro", help="Show post-mortem for a completed task")
    retro_p.add_argument("task_id", help="Task ID to show retro for")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Import and dispatch to command handlers
    if args.command == "init":
        from agentwork.commands.init import run
        run(args)
    elif args.command == "assign":
        from agentwork.commands.assign import run
        run(args)
    elif args.command == "status":
        from agentwork.commands.status import run
        run(args)
    elif args.command == "accept":
        from agentwork.commands.accept import run
        run(args)
    elif args.command == "audit":
        from agentwork.commands.audit import run
        run(args)
    elif args.command == "agents":
        from agentwork.commands.agents import run
        run(args)
    elif args.command == "register":
        from agentwork.commands.register import run
        run(args)
    elif args.command == "retro":
        from agentwork.commands.retro import run
        run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
