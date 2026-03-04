#!/usr/bin/env python3
"""
generate_attestation.py - Generates a portable, signed reputation attestation.

Reads reputation/ledger.json, extracts the specified agent's tensor,
composite_R, and project history, then produces an HMAC-SHA256 signed
JSON attestation blob.

Usage:
  # Generate attestation to stdout
  python scripts/generate_attestation.py <agent_id>

  # Generate attestation to a file
  python scripts/generate_attestation.py <agent_id> --output attestation.json

  # Verify an existing attestation
  python scripts/generate_attestation.py <agent_id> --verify attestation.json

The signing secret is read from the ATTESTATION_SECRET environment variable.
If not set, a default key is used (not suitable for production).
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SECRET_ENV = "ATTESTATION_SECRET"


def load_json_file(filepath):
    """Load a JSON file or exit with an error."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {filepath}: {e}", file=sys.stderr)
        sys.exit(1)


def get_signing_secret():
    """Get the HMAC signing secret from environment. Fails if not set."""
    secret = os.environ.get(DEFAULT_SECRET_ENV)
    if not secret:
        print(
            f"ERROR: {DEFAULT_SECRET_ENV} environment variable is required.\n"
            f"Generate a secret with: python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
            f"Then: export {DEFAULT_SECRET_ENV}=<your-secret>",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(secret) < 32:
        print(
            f"WARNING: {DEFAULT_SECRET_ENV} is shorter than 32 characters. "
            f"Use a stronger secret for production.",
            file=sys.stderr,
        )
    return secret


def build_attestation_payload(agent_id, ledger):
    """Build the attestation payload from the ledger.

    Extracts the agent's tensor (current values), composite_R, task/audit
    counts, anomaly summary, and full score history.
    """
    agents = ledger.get("agents", {})
    agent_data = agents.get(agent_id)

    if not agent_data:
        print(f"ERROR: Agent '{agent_id}' not found in ledger.", file=sys.stderr)
        available = sorted(agents.keys())
        if available:
            print(f"Available agents: {', '.join(available)}", file=sys.stderr)
        else:
            print("The ledger contains no agents.", file=sys.stderr)
        sys.exit(1)

    # Extract current tensor values
    tensor = agent_data.get("tensor", {})
    tensor_summary = {}
    for dim_name, dim_data in tensor.items():
        if isinstance(dim_data, dict):
            tensor_summary[dim_name] = {
                "current": dim_data.get("current", 0.0),
                "history_length": len(dim_data.get("scores", [])),
                "computed_by": dim_data.get("computed_by", "unknown"),
            }
        else:
            tensor_summary[dim_name] = {"current": dim_data, "history_length": 0}

    # Build project history from tensor score arrays
    score_history = {}
    for dim_name, dim_data in tensor.items():
        if isinstance(dim_data, dict):
            score_history[dim_name] = dim_data.get("scores", [])

    # Anomaly summary
    anomaly_flags = agent_data.get("anomaly_flags", [])
    anomaly_summary = {}
    for flag in anomaly_flags:
        atype = flag.get("type", "unknown")
        anomaly_summary[atype] = anomaly_summary.get(atype, 0) + 1

    payload = {
        "version": "1.0",
        "agent_id": agent_id,
        "composite_R": agent_data.get("composite_R", 0.0),
        "tensor": tensor_summary,
        "tasks_completed": agent_data.get("tasks_completed", 0),
        "tasks_assigned": agent_data.get("tasks_assigned", 0),
        "audits_performed": agent_data.get("audits_performed", 0),
        "anomaly_counts": anomaly_summary,
        "total_anomalies": len(anomaly_flags),
        "score_history": score_history,
        "last_active": agent_data.get("last_active"),
        "ledger_last_updated": ledger.get("last_updated"),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }

    return payload


def sign_payload(payload, secret):
    """Create an HMAC-SHA256 signature of the canonicalized payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


def generate_attestation(agent_id, ledger):
    """Generate the complete signed attestation document."""
    secret = get_signing_secret()
    payload = build_attestation_payload(agent_id, ledger)
    signature = sign_payload(payload, secret)

    return {
        "attestation": payload,
        "signature": signature,
        "algorithm": "HMAC-SHA256",
    }


def verify_attestation(attestation_doc, secret=None):
    """Verify the signature on an attestation document.

    Returns True if the signature matches; False otherwise.
    """
    if secret is None:
        secret = get_signing_secret()

    payload = attestation_doc.get("attestation", {})
    claimed_sig = attestation_doc.get("signature", "")
    algorithm = attestation_doc.get("algorithm", "")

    if algorithm != "HMAC-SHA256":
        print(f"ERROR: Unsupported algorithm: {algorithm}", file=sys.stderr)
        return False

    expected_sig = sign_payload(payload, secret)
    return hmac.compare_digest(claimed_sig, expected_sig)


def main():
    parser = argparse.ArgumentParser(
        description="Generate or verify a portable reputation attestation for an agent."
    )
    parser.add_argument(
        "agent_id",
        help="The agent ID to generate the attestation for",
    )
    parser.add_argument(
        "--output", "-o",
        help="Write attestation to this file instead of stdout",
        default=None,
    )
    parser.add_argument(
        "--verify",
        help="Path to an existing attestation file to verify (instead of generating)",
        default=None,
    )
    parser.add_argument(
        "--ledger",
        help="Path to ledger.json (default: reputation/ledger.json)",
        default=None,
    )

    args = parser.parse_args()

    # Verification mode
    if args.verify:
        attestation_doc = load_json_file(args.verify)
        is_valid = verify_attestation(attestation_doc)
        if is_valid:
            print(f"VALID: Attestation for '{args.agent_id}' has a valid signature.")
            payload = attestation_doc.get("attestation", {})
            print(f"  composite_R: {payload.get('composite_R', 'N/A')}")
            print(f"  issued_at:   {payload.get('issued_at', 'N/A')}")
            sys.exit(0)
        else:
            print(
                f"INVALID: Attestation signature does not match.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Generation mode
    repo_root = Path(__file__).resolve().parent.parent
    if args.ledger:
        ledger_path = Path(args.ledger)
    else:
        ledger_path = repo_root / "reputation" / "ledger.json"

    ledger = load_json_file(ledger_path)
    attestation = generate_attestation(args.agent_id, ledger)
    output_json = json.dumps(attestation, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_json)
            f.write("\n")
        print(f"Attestation written to {output_path}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
