#!/usr/bin/env python3
"""Local test harness for running a single agent against the EU AI Act.

Bypasses Docker/GitHub and directly tests the skill + LLM pipeline.
Parses task-001 (Articles 1-4) using the Claude API.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 test_local_agent.py
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent_worker.llm import LLMClient
from agent_worker.schema_validator import validate_output
from skills.eu_ai_act_parser import EuAiActParserSkill

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_local_agent")

# ─── Config ──────────────────────────────────────────────────────────
AGENT_ID = "agent-alpha"
TASK_ID = "task-001"
TASK_TITLE = "Parse Title I — General Provisions (Articles 1-4)"
SOURCE_DIR = PROJECT_ROOT / "source"
OUTPUT_DIR = PROJECT_ROOT / "output" / "sections"
OUTPUT_SCHEMA = PROJECT_ROOT / "output" / "schema.json"


def extract_articles_section(full_text: str, start_article: int, end_article: int) -> str:
    """Extract a range of articles from the full EU AI Act text.

    Uses regex to find article boundaries in Bulgarian ('Член N').
    """
    # Find all article positions
    pattern = re.compile(r'(?:^|\n)\s*Член\s+(\d+)\b', re.MULTILINE)
    matches = list(pattern.finditer(full_text))

    start_pos = None
    end_pos = None

    for i, match in enumerate(matches):
        art_num = int(match.group(1))
        if art_num == start_article and start_pos is None:
            start_pos = match.start()
        if art_num == end_article + 1 and end_pos is None:
            end_pos = match.start()
            break

    if start_pos is None:
        logger.warning("Could not find Article %d, using full text", start_article)
        return full_text

    if end_pos is None:
        # Take from start_pos to 20000 chars or end
        end_pos = min(start_pos + 20000, len(full_text))

    section = full_text[start_pos:end_pos]
    logger.info(
        "Extracted Articles %d-%d: %d characters (positions %d-%d)",
        start_article, end_article, len(section), start_pos, end_pos,
    )
    return section


def run_test():
    """Run a single agent parsing task-001 (Articles 1-4)."""
    # 1. Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\n❌ ANTHROPIC_API_KEY not set.")
        print("   Run: export ANTHROPIC_API_KEY=sk-ant-...")
        print("   Then re-run: python3 test_local_agent.py")
        sys.exit(1)

    provider = "anthropic"
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    logger.info("Using provider=%s, model=%s", provider, model)

    # 2. Initialize LLM client
    llm = LLMClient(provider=provider, api_key=api_key)
    logger.info("LLM client initialized: %s", provider)

    # 3. Load source material
    source_file = SOURCE_DIR / "eu-ai-act-full-text.md"
    if not source_file.is_file():
        print(f"\n❌ Source file not found: {source_file}")
        sys.exit(1)

    full_text = source_file.read_text(encoding="utf-8")
    logger.info("Loaded source: %d characters", len(full_text))

    # 4. Extract just Articles 1-4 for task-001
    section_text = extract_articles_section(full_text, 1, 4)

    # Also include the preamble/recitals for context (first ~5000 chars before Article 1)
    preamble_end = full_text.find("Член 1")
    if preamble_end > 0:
        # Include last portion of preamble for context
        preamble_start = max(0, preamble_end - 3000)
        preamble = full_text[preamble_start:preamble_end]
        section_text = preamble + "\n\n" + section_text

    logger.info("Section text for task-001: %d characters", len(section_text))

    # 5. Write section-specific source file for the skill
    section_source = SOURCE_DIR / "task-001-articles-1-4.md"
    section_source.write_text(section_text, encoding="utf-8")
    logger.info("Wrote section source: %s", section_source)

    # 6. Initialize skill and run
    skill = EuAiActParserSkill(llm, str(SOURCE_DIR), str(OUTPUT_DIR))

    task_metadata = {
        "task_id": TASK_ID,
        "section_id": TASK_ID,
        "agent_id": AGENT_ID,
        "title": TASK_TITLE,
        "source_files": ["task-001-articles-1-4.md"],
        "sections": [TASK_ID],
    }

    logger.info("=" * 60)
    logger.info("EXECUTING: %s", TASK_TITLE)
    logger.info("Agent: %s | Task: %s", AGENT_ID, TASK_ID)
    logger.info("=" * 60)

    result = skill.execute(task_metadata)

    # 7. Validate against schema
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    is_valid, errors = validate_output(result, str(OUTPUT_SCHEMA))

    if is_valid:
        logger.info("✅ Schema validation PASSED")
    else:
        logger.error("❌ Schema validation FAILED:")
        for err in errors:
            logger.error("  - %s", err)

    # 8. Write output
    output_path = OUTPUT_DIR / f"{TASK_ID}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info("Output written: %s", output_path)

    # 9. Print summary
    articles = result.get("articles", [])
    print("\n" + "=" * 60)
    print(f"  TEST RESULTS: {TASK_TITLE}")
    print("=" * 60)
    print(f"  Agent:            {AGENT_ID}")
    print(f"  Task:             {TASK_ID}")
    print(f"  Schema valid:     {'✅ YES' if is_valid else '❌ NO'}")
    print(f"  Articles parsed:  {len(articles)}")
    print(f"  Token usage:      {llm.token_usage}")
    print(f"  Output file:      {output_path}")
    print()

    for art in articles:
        risk = art.get("risk_category", "N/A")
        obligs = len(art.get("obligations", []))
        xrefs = len(art.get("cross_references", []))
        defs = len(art.get("key_definitions", []))
        print(f"  Article {art['article_number']}: {art['title']}")
        print(f"    Risk: {risk} | Obligations: {obligs} | Cross-refs: {xrefs} | Definitions: {defs}")
        print(f"    Text: {art.get('text', '')[:120]}...")
        print()

    if result.get("summary"):
        print(f"  Summary: {result['summary'][:300]}")
    print()

    if errors:
        print("  VALIDATION ERRORS:")
        for err in errors:
            print(f"    - {err}")
        print()

    # Cleanup temp source file
    if section_source.is_file():
        section_source.unlink()

    return is_valid


if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
