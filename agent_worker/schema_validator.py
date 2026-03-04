from __future__ import annotations

"""Schema validation for agent outputs, audit reports, and retrospectives.

Uses JSON Schema (Draft-07) validation via the jsonschema library. Each
validator loads its schema from the repo's schema files and returns a
tuple of (is_valid, list_of_error_messages).
"""

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)


def _load_schema(schema_path: str) -> dict:
    """Load a JSON schema file from disk.

    Args:
        schema_path: Absolute or relative path to the JSON schema file.

    Returns:
        The parsed JSON schema as a dict.

    Raises:
        FileNotFoundError: If the schema file does not exist.
        json.JSONDecodeError: If the schema file is not valid JSON.
    """
    path = Path(schema_path)
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    with open(path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    logger.debug("Loaded schema from %s (title: %s)", schema_path, schema.get("title", "unknown"))
    return schema


def _validate(data: Any, schema_path: str) -> tuple[bool, list[str]]:
    """Validate data against a JSON schema.

    Args:
        data: The data to validate (typically a dict).
        schema_path: Path to the JSON schema file.

    Returns:
        A tuple of (is_valid: bool, errors: list[str]).
        If is_valid is True, errors will be an empty list.
        If is_valid is False, errors will contain human-readable error descriptions.
    """
    try:
        schema = _load_schema(schema_path)
    except FileNotFoundError as exc:
        return False, [str(exc)]
    except json.JSONDecodeError as exc:
        return False, [f"Invalid JSON in schema file {schema_path}: {exc}"]

    validator_class = jsonschema.Draft7Validator
    validator = validator_class(schema)

    errors = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path_str = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        error_msg = f"[{path_str}] {error.message}"
        errors.append(error_msg)
        logger.warning("Validation error: %s", error_msg)

    is_valid = len(errors) == 0
    if is_valid:
        logger.info("Validation passed against schema: %s", schema_path)
    else:
        logger.warning("Validation failed with %d error(s) against schema: %s", len(errors), schema_path)

    return is_valid, errors


def validate_output(data: Any, schema_path: str) -> tuple[bool, list[str]]:
    """Validate a section output against output/schema.json.

    Args:
        data: The parsed section output data.
        schema_path: Path to the output schema file.

    Returns:
        A tuple of (is_valid, errors).
    """
    logger.info("Validating section output against: %s", schema_path)
    return _validate(data, schema_path)


def validate_audit(data: Any, schema_path: str) -> tuple[bool, list[str]]:
    """Validate an audit report against audits/schema.json.

    Args:
        data: The audit report data.
        schema_path: Path to the audit schema file.

    Returns:
        A tuple of (is_valid, errors).
    """
    logger.info("Validating audit report against: %s", schema_path)
    return _validate(data, schema_path)


def validate_retro(data: Any, schema_path: str) -> tuple[bool, list[str]]:
    """Validate a retrospective report against retros/schema.json.

    Args:
        data: The retrospective report data.
        schema_path: Path to the retro schema file.

    Returns:
        A tuple of (is_valid, errors).
    """
    logger.info("Validating retrospective report against: %s", schema_path)
    return _validate(data, schema_path)
