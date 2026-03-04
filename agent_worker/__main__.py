"""Entry point for the agent_worker module. Parses CLI arguments and dispatches to AgentWorker."""

import argparse
import logging
import sys

from agent_worker.worker import AgentWorker


def configure_logging(level: str) -> None:
    """Configure logging with the given level string."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands for each agent operation."""
    parser = argparse.ArgumentParser(
        prog="agent_worker",
        description="AgentWork agent runtime - claim, execute, audit, submit, and retro tasks.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # claim
    claim_parser = subparsers.add_parser("claim", help="Claim a task from the manifest")
    claim_parser.add_argument("--task-id", required=True, help="The task ID to claim")

    # execute
    execute_parser = subparsers.add_parser("execute", help="Execute a claimed task using the configured skill")
    execute_parser.add_argument("--task-id", required=True, help="The task ID to execute")

    # audit
    audit_parser = subparsers.add_parser("audit", help="Peer-audit another agent's PR output")
    audit_parser.add_argument("--pr-number", required=True, type=int, help="The PR number to audit")

    # submit
    submit_parser = subparsers.add_parser("submit", help="Validate and submit task output as a PR")
    submit_parser.add_argument("--task-id", required=True, help="The task ID to submit")

    # retro
    retro_parser = subparsers.add_parser("retro", help="Generate and submit a post-mortem retrospective")
    retro_parser.add_argument("--task-id", required=True, help="The task ID to write a retro for")

    return parser


def main() -> None:
    """Parse arguments and dispatch to the appropriate AgentWorker method."""
    parser = build_parser()
    args = parser.parse_args()

    configure_logging("INFO")
    logger = logging.getLogger("agent_worker")

    try:
        worker = AgentWorker()
    except Exception as exc:
        logger.error("Failed to initialize AgentWorker: %s", exc)
        sys.exit(1)

    command = args.command
    logger.info("Dispatching command: %s", command)

    try:
        if command == "claim":
            worker.claim_task(args.task_id)
        elif command == "execute":
            worker.execute(args.task_id)
        elif command == "audit":
            worker.audit(args.pr_number)
        elif command == "submit":
            worker.submit(args.task_id)
        elif command == "retro":
            worker.submit_retro(args.task_id)
        else:
            logger.error("Unknown command: %s", command)
            sys.exit(1)
    except Exception as exc:
        logger.error("Command '%s' failed: %s", command, exc, exc_info=True)
        sys.exit(1)

    logger.info("Command '%s' completed successfully.", command)


if __name__ == "__main__":
    main()
