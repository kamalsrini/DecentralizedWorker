"""Reputation tensor operations for the CLI."""

import json
from datetime import datetime, timezone
from pathlib import Path

DECAY = 0.9
WEIGHTS = {
    "technical_accuracy": 0.4,
    "collaborative_signal": 0.25,
    "reliability": 0.25,
    "audit_contribution": 0.1,
}


def compute_composite(tensor: dict) -> float:
    """Compute composite R from tensor dimensions."""
    score = 0.0
    for dim, weight in WEIGHTS.items():
        val = tensor.get(dim, 0.5)
        if isinstance(val, dict):
            val = val.get("current", 0.5)
        score += val * weight
    return round(score, 4)


def init_agent_entry(agent_id: str, agent_type: str = "ai_agent", owner: str = "") -> dict:
    """Create a fresh agent entry for the ledger."""
    return {
        "tensor": {
            "technical_accuracy": {
                "description": "CI/schema pass rate",
                "scores": [],
                "current": 0.5,
                "computed_by": "automated",
            },
            "collaborative_signal": {
                "description": "PR revision requests (lower = better)",
                "scores": [],
                "current": 0.5,
                "computed_by": "peer_audit",
            },
            "reliability": {
                "description": "On-time delivery rate",
                "scores": [],
                "current": 0.5,
                "computed_by": "automated",
            },
            "audit_contribution": {
                "description": "Quality of peer audits performed",
                "scores": [],
                "current": 0.5,
                "computed_by": "reviewer",
            },
        },
        "composite_R": 0.5,
        "anomaly_flags": [],
        "tasks_completed": 0,
        "tasks_assigned": 0,
        "audits_performed": 0,
        "last_active": datetime.now(timezone.utc).isoformat(),
        "type": agent_type,
        "owner": owner,
    }


def format_agent_table(agents: dict) -> str:
    """Format agents as a readable table."""
    if not agents:
        return "  No agents registered."

    lines = []
    header = f"  {'Agent ID':<20} {'Type':<10} {'R':<8} {'Tech':<8} {'Collab':<8} {'Reliab':<8} {'Audit':<8} {'Tasks'}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for agent_id, data in sorted(agents.items()):
        tensor = data.get("tensor", {})

        def _get(dim):
            v = tensor.get(dim, {})
            return v.get("current", 0.5) if isinstance(v, dict) else v

        r = data.get("composite_R", 0.0)
        t = data.get("tasks_completed", 0)
        agent_type = data.get("type", "?")
        lines.append(
            f"  {agent_id:<20} {agent_type:<10} {r:<8.3f} "
            f"{_get('technical_accuracy'):<8.3f} "
            f"{_get('collaborative_signal'):<8.3f} "
            f"{_get('reliability'):<8.3f} "
            f"{_get('audit_contribution'):<8.3f} {t}"
        )

    return "\n".join(lines)
