"""agentwork agents — List registered agents and their reputation."""

from agentwork.config import Config
from agentwork.reputation import format_agent_table


def run(args):
    cfg = Config()
    ledger = cfg.load_ledger()
    agents = ledger.get("agents", {})

    print(f"\n\033[1mRegistered Agents\033[0m ({len(agents)})\n")
    print(format_agent_table(agents))

    last = ledger.get("last_updated")
    if last:
        print(f"\n  Last updated: {last}")
    print()
