from __future__ import annotations

"""EU AI Act Parser Skill.

Parses assigned articles from EU AI Act source text, extracting:
- Obligations and requirements
- Risk categories (unacceptable, high, limited, minimal)
- Cross-references between articles
- Key definitions and terms

Uses LLM-based structured extraction with carefully designed prompts.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from skills.base import BaseSkill

logger = logging.getLogger(__name__)

PARSE_SYSTEM_PROMPT = """You are an expert legal analyst specializing in the EU AI Act (Regulation (EU) 2024/1689).

Your task is to parse specific articles from the EU AI Act source text and extract structured information.

For each article, you must extract:
1. **article_number**: The article number as it appears in the text.
2. **title**: The official title of the article.
3. **text**: The full text of the article.
4. **obligations**: A list of specific obligations, duties, or requirements stated in the article. Each obligation should be a clear, standalone statement.
5. **applies_to**: Who the article applies to (e.g., "AI providers", "deployers", "importers", "distributors", "notified bodies", "market surveillance authorities", "EU institutions").
6. **risk_category**: The risk category this article relates to. Must be one of: "unacceptable", "high", "limited", "minimal", or null if the article does not relate to a specific risk category.
7. **cross_references**: A list of other articles, annexes, or regulations referenced within this article (e.g., "Article 6", "Annex III", "Regulation (EU) 2016/679").
8. **key_definitions**: A list of terms defined or significantly elaborated in this article, each with the term and its definition.

IMPORTANT RULES:
- Be precise and factual. Only extract information explicitly stated in the text.
- Do not infer obligations that are not clearly stated.
- For the "text" field, provide a CONCISE summary (2-3 sentences) of the article content, NOT the full verbatim text. This keeps the output manageable.
- For articles with many definitions (e.g., Article 3), extract the key_definitions array but keep the "text" field as a brief description of what the article defines.
- For cross_references, include ALL references to other articles, sections, annexes, or external regulations.
- For risk_category, only assign a category if the article is explicitly about that risk tier.
- Return valid JSON only, with no markdown formatting or code blocks.
- Keep total response under 8000 tokens to avoid truncation.

Return your response as a JSON object with this exact structure:
{
  "articles": [
    {
      "article_number": "string",
      "title": "string",
      "text": "string",
      "obligations": ["string"],
      "applies_to": ["string"],
      "risk_category": "string or null",
      "cross_references": ["string"],
      "key_definitions": [{"term": "string", "definition": "string"}]
    }
  ],
  "summary": "A concise summary of the key themes and requirements across all parsed articles."
}"""

AUDIT_SYSTEM_PROMPT = """You are an expert auditor reviewing parsed legal content from the EU AI Act.

Your task is to compare a structured parsed output against the original source text and identify:

1. **Schema compliance**: Whether all required fields are present and correctly typed.
2. **Factual accuracy**: Whether the parsed obligations, risk categories, and definitions accurately reflect the source text. Flag any:
   - Missing obligations that are clearly stated in the source
   - Incorrect risk category assignments
   - Fabricated or hallucinated content not found in the source
   - Misattributed cross-references
3. **Cross-reference integrity**: Whether all cross-references in the source text are captured and correctly cited.
4. **Completeness**: Whether any articles or significant content was omitted.

For each issue found, assign a severity:
- "critical": Factual errors, hallucinated content, or wrong risk categories
- "major": Missing obligations, incomplete cross-references, or significant omissions
- "minor": Stylistic issues, minor formatting problems, or trivial omissions

Provide an overall assessment:
- "approve": No critical or major issues found
- "request_changes": Major issues found but the work is salvageable
- "reject": Critical issues that require complete rework

Also provide a confidence score (0.0 to 1.0) representing how confident you are in your audit.

IMPORTANT: Be thorough but fair. Only flag genuine issues, not stylistic preferences.
Return valid JSON only, with no markdown formatting or code blocks.

Return your response as a JSON object with this exact structure:
{
  "schema_valid": true/false,
  "factual_issues": [
    {
      "article": "Article N",
      "issue": "Description of the issue",
      "severity": "critical|major|minor",
      "suggestion": "How to fix this"
    }
  ],
  "cross_ref_issues": ["Description of cross-reference issue"],
  "overall_assessment": "approve|request_changes|reject",
  "confidence": 0.0-1.0
}"""


class EuAiActParserSkill(BaseSkill):
    """Skill for parsing EU AI Act articles into structured format.

    Extracts obligations, risk categories, cross-references, and key definitions
    from source text using LLM-based structured prompts.
    """

    def execute(self, task_metadata: dict) -> dict:
        """Parse assigned articles from EU AI Act source text.

        Reads source material files, sends them to the LLM for structured extraction,
        and returns a validated output conforming to output/schema.json.

        Args:
            task_metadata: Must contain:
                - task_id (str): The task identifier.
                - section_id (str): The section being parsed.
                - agent_id (str): The agent performing the task.
                - source_files (list[str]): Source filenames to process.

        Returns:
            A dict conforming to output/schema.json with parsed articles.

        Raises:
            FileNotFoundError: If source files cannot be found.
            RuntimeError: If LLM parsing fails.
        """
        task_id = task_metadata["task_id"]
        section_id = task_metadata.get("section_id", task_id)
        agent_id = task_metadata.get("agent_id", "unknown")
        source_files = task_metadata.get("source_files", [])

        logger.info("Executing EU AI Act parser for task %s, section %s", task_id, section_id)

        # Collect all source material
        source_texts = []
        for filename in source_files:
            try:
                text = self.read_source_file(filename)
                source_texts.append(text)
                logger.info("Read source file: %s (%d chars)", filename, len(text))
            except FileNotFoundError:
                logger.error("Source file not found: %s", filename)
                raise

        if not source_texts:
            # Try reading all available source files
            available = self.list_source_files()
            if available:
                logger.info("No specific source files given, reading all %d available files.", len(available))
                for filename in available:
                    text = self.read_source_file(filename)
                    source_texts.append(text)
            else:
                raise FileNotFoundError(
                    f"No source files found in {self.source_dir} and none specified in task metadata."
                )

        combined_source = "\n\n---\n\n".join(source_texts)

        # Build user prompt with section-specific instructions
        section_instruction = ""
        if section_id:
            section_instruction = f"\nFocus on parsing content for section: {section_id}\n"

        user_prompt = (
            f"Parse the following EU AI Act source text and extract structured information "
            f"for all articles found.\n"
            f"{section_instruction}\n"
            f"SOURCE TEXT:\n\n{combined_source}"
        )

        # Call LLM for extraction
        logger.info("Sending %d chars to LLM for structured extraction.", len(user_prompt))
        raw_response = self.llm.complete(PARSE_SYSTEM_PROMPT, user_prompt)

        # Parse the LLM response as JSON
        parsed = self._parse_llm_json(raw_response)

        # Build the output conforming to output/schema.json
        output = {
            "section_id": section_id,
            "title": task_metadata.get("title", f"Section {section_id}"),
            "articles": parsed.get("articles", []),
            "summary": parsed.get("summary", ""),
            "parsed_by": agent_id,
            "parsed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Ensure each article has all required fields with defaults
        for article in output["articles"]:
            article.setdefault("obligations", [])
            article.setdefault("applies_to", [])
            article.setdefault("risk_category", None)
            article.setdefault("cross_references", [])
            article.setdefault("key_definitions", [])

        logger.info(
            "Parsed %d articles for section %s. Summary length: %d chars.",
            len(output["articles"]),
            section_id,
            len(output["summary"]),
        )

        return output

    def audit(self, output_data: dict, source_data: str) -> dict:
        """Audit another agent's parsed output against the original source text.

        Sends the parsed output and source material to the LLM for structured
        comparison and returns an audit findings report.

        Args:
            output_data: The structured output to audit (conforming to output/schema.json).
            source_data: The raw source material text for comparison.

        Returns:
            A dict of audit findings conforming to audits/schema.json findings structure:
            {
                "schema_valid": bool,
                "factual_issues": [...],
                "cross_ref_issues": [...],
                "overall_assessment": str,
                "confidence": float
            }
        """
        logger.info(
            "Auditing output for section '%s' (parsed by %s, %d articles)",
            output_data.get("section_id", "unknown"),
            output_data.get("parsed_by", "unknown"),
            len(output_data.get("articles", [])),
        )

        # Format the output data as readable JSON for the LLM
        formatted_output = json.dumps(output_data, indent=2)

        user_prompt = (
            f"AUDIT TASK: Compare the following parsed output against the original source text.\n\n"
            f"PARSED OUTPUT:\n{formatted_output}\n\n"
            f"ORIGINAL SOURCE TEXT:\n{source_data}\n\n"
            f"Identify any factual errors, missing content, incorrect cross-references, "
            f"or schema compliance issues. Return your findings as structured JSON."
        )

        logger.info("Sending audit request to LLM (%d chars).", len(user_prompt))
        raw_response = self.llm.complete(AUDIT_SYSTEM_PROMPT, user_prompt)

        # Parse the LLM audit response
        findings = self._parse_llm_json(raw_response)

        # Ensure required fields with sensible defaults
        findings.setdefault("schema_valid", True)
        findings.setdefault("factual_issues", [])
        findings.setdefault("cross_ref_issues", [])
        findings.setdefault("overall_assessment", "approve")
        findings.setdefault("confidence", 0.5)

        # Clamp confidence to valid range
        findings["confidence"] = max(0.0, min(1.0, float(findings["confidence"])))

        # Validate severity values
        valid_severities = {"critical", "major", "minor"}
        for issue in findings["factual_issues"]:
            if issue.get("severity") not in valid_severities:
                issue["severity"] = "minor"
            issue.setdefault("article", "unknown")
            issue.setdefault("issue", "Unspecified issue")

        # Validate overall_assessment
        valid_assessments = {"approve", "request_changes", "reject"}
        if findings["overall_assessment"] not in valid_assessments:
            findings["overall_assessment"] = "request_changes"

        logger.info(
            "Audit complete: assessment=%s, confidence=%.2f, issues=%d",
            findings["overall_assessment"],
            findings["confidence"],
            len(findings["factual_issues"]),
        )

        return findings

    def _parse_llm_json(self, raw_response: str) -> dict:
        """Parse JSON from an LLM response, handling common formatting issues.

        The LLM sometimes wraps JSON in markdown code blocks or includes
        preamble text. This method strips those artifacts and parses the JSON.

        Args:
            raw_response: The raw text response from the LLM.

        Returns:
            The parsed JSON as a dict.

        Raises:
            RuntimeError: If the response cannot be parsed as valid JSON.
        """
        text = raw_response.strip()

        # Strip markdown code blocks if present
        if text.startswith("```json"):
            text = text[len("```json"):].strip()
        if text.startswith("```"):
            text = text[len("```"):].strip()
        if text.endswith("```"):
            text = text[:-len("```")].strip()

        # Try direct JSON parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        # Last resort: try to find a JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                arr = json.loads(text[start:end + 1])
                return {"articles": arr, "summary": ""}
            except json.JSONDecodeError:
                pass

        # Handle truncated JSON (e.g., from token limit)
        # Try to repair by closing open brackets/braces
        repaired = self._repair_truncated_json(text)
        if repaired:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        raise RuntimeError(
            f"Failed to parse LLM response as JSON. Response starts with: {text[:200]}"
        )

    @staticmethod
    def _repair_truncated_json(text: str) -> str:
        """Attempt to repair truncated JSON by closing open brackets and braces.

        Args:
            text: Potentially truncated JSON string.

        Returns:
            Repaired JSON string, or empty string if repair fails.
        """
        # Find the start of the JSON object
        start = text.find("{")
        if start == -1:
            return ""

        text = text[start:]

        # Track nesting to figure out what needs closing
        in_string = False
        escape = False
        stack = []

        for ch in text:
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()

        if not stack:
            return text  # Already balanced

        # Close any open string
        if in_string:
            text += '"'

        # Find a clean cut point: last complete object/value
        # Try to cut at the last complete article object
        last_complete = text.rfind('},')
        if last_complete > 0:
            text = text[:last_complete + 1]
            # Recount the stack
            stack = []
            in_string = False
            escape = False
            for ch in text:
                if escape:
                    escape = False
                    continue
                if ch == '\\' and in_string:
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch in ('{', '['):
                    stack.append(ch)
                elif ch == '}' and stack and stack[-1] == '{':
                    stack.pop()
                elif ch == ']' and stack and stack[-1] == '[':
                    stack.pop()

        # Add a summary field if we're inside the articles array
        # and close all open brackets
        closers = []
        for bracket in reversed(stack):
            if bracket == '[':
                closers.append(']')
            elif bracket == '{':
                closers.append('}')

        # If we're closing an articles array, inject a summary
        if len(closers) >= 2 and closers[0] == ']':
            text += closers[0]  # close articles array
            text += ', "summary": "(truncated — output exceeded token limit)"'
            text += ''.join(closers[1:])
        else:
            text += ''.join(closers)

        return text
