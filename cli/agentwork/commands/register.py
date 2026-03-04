"""agentwork register <agent-id> — Register a new agent."""

import sys
from datetime import datetime, timezone

from agentwork.config import Config
from agentwork.reputation import init_agent_entry


def run(args):
    cfg = Config()
    ledger = cfg.load_ledger()
    agents = ledger.get("agents", {})

    if args.agent_id in agents:
        print(f"\033[33mAgent '{args.agent_id}' is already registered.\033[0m")
        sys.exit(1)

    agents[args.agent_id] = init_agent_entry(
        args.agent_id,
        agent_type=args.agent_type,
        owner=args.owner,
    )
    ledger["agents"] = agents
    ledger["last_updated"] = datetime.now(timezone.utc).isoformat()

    cfg.save_ledger(ledger)

    print(f"\n\033[32mRegistered agent: {args.agent_id}\033[0m")
    print(f"  Type: {args.agent_type}")
    print(f"  Owner: {args.owner or '(not set)'}")
    print(f"  Initial composite_R: 0.5")
    print()
