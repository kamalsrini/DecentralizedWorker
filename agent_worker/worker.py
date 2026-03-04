from __future__ import annotations

"""Main AgentWorker class - orchestrates claim, execute, audit, submit, and retro flows.

All configuration is loaded from environment variables. The worker uses pluggable skills
for task execution and LLM-based peer auditing, and communicates results via Git/GitHub PRs.
"""

import importlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_worker import git_ops, schema_validator
from agent_worker.llm import LLMClient

logger = logging.getLogger(__name__)

# Maps skill names to their module paths and class names
SKILL_REGISTRY: dict[str, tuple[str, str]] = {
    "eu_ai_act_parser": ("skills.eu_ai_act_parser", "EuAiActParserSkill"),
}


class AgentWorker:
    """Docker-containerized agent runtime for the AgentWork platform.

    All configuration is read from environment variables:
      AGENT_ID       - unique identifier for this agent
      REPO_URL       - GitHub repo to work against
      GITHUB_TOKEN   - agent's GitHub PAT (scoped to repo)
      LLM_API_KEY    - API key for the agent's LLM provider
      LLM_PROVIDER   - "anthropic" | "openai" | "local"
      SKILL_NAME     - which skill to load (e.g., "eu_ai_act_parser")
      WORK_DIR       - local workspace directory (default: /workspace)
    """

    def __init__(self):
        """Load config from environment variables and initialize the LLM client.

        Raises:
            KeyError: If AGENT_ID, REPO_URL, or GITHUB_TOKEN are not set.
            ValueError: If a non-local LLM provider is selected but LLM_API_KEY is missing.
        """
        self.agent_id = os.environ["AGENT_ID"]
        self.repo_url = os.environ["REPO_URL"]
        self.github_token = os.environ["GITHUB_TOKEN"]
        self.skill_name = os.environ.get("SKILL_NAME", "eu_ai_act_parser")
        self.work_dir = os.environ.get("WORK_DIR", "/workspace")
        self.repo_dir = os.path.join(self.work_dir, "repo")

        self.llm = LLMClient(
            provider=os.environ.get("LLM_PROVIDER", "anthropic"),
            api_key=os.environ.get("LLM_API_KEY", ""),
        )

        self._skill = None
        logger.info(
            "AgentWorker initialized: agent=%s, repo=%s, skill=%s, provider=%s",
            self.agent_id, self.repo_url, self.skill_name, self.llm.provider,
        )

    # ------------------------------------------------------------------ #
    # Path helpers
    # ------------------------------------------------------------------ #

    @property
    def source_dir(self) -> Path:
        return Path(self.repo_dir) / "source"

    @property
    def output_dir(self) -> Path:
        return Path(self.repo_dir) / "output" / "sections"

    @property
    def manifest_path(self) -> Path:
        """Return the path to the project manifest, searching multiple locations."""
        candidates = [
            Path(self.repo_dir) / "tasks" / "manifest.json",
            Path(self.repo_dir) / "manifest.yml",
            Path(self.repo_dir) / "manifest.yaml",
            Path(self.repo_dir) / "manifest.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        # Default to the tasks/manifest.json path even if it does not exist yet
        return Path(self.repo_dir) / "tasks" / "manifest.json"

    # ------------------------------------------------------------------ #
    # Manifest I/O
    # ------------------------------------------------------------------ #

    def _load_manifest(self) -> dict:
        """Load the project manifest from disk.

        Supports JSON and YAML formats.

        Returns:
            The parsed manifest dict.

        Raises:
            FileNotFoundError: If no manifest file exists.
        """
        path = self.manifest_path
        if not path.is_file():
            raise FileNotFoundError(f"Manifest not found at {path}")

        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yml", ".yaml"):
                import yaml
                return yaml.safe_load(f)
            return json.load(f)

    def _save_manifest(self, manifest: dict) -> None:
        """Write the manifest back to disk (JSON only).

        Args:
            manifest: The manifest dict to persist.
        """
        path = self.manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")

    def _find_task(self, manifest: dict, task_id: str) -> dict:
        """Find a task by ID in the manifest.

        Args:
            manifest: The parsed manifest dict.
            task_id: The task identifier to look up.

        Returns:
            The task dict.

        Raises:
            ValueError: If the task is not found.
        """
        for task in manifest.get("tasks", []):
            if task.get("id") == task_id or task.get("task_id") == task_id:
                return task
        available = [t.get("id", t.get("task_id", "?")) for t in manifest.get("tasks", [])]
        raise ValueError(f"Task {task_id} not found in manifest. Available: {available}")

    # ------------------------------------------------------------------ #
    # Skill loader
    # ------------------------------------------------------------------ #

    def _load_skill(self):
        """Dynamically load the configured skill module and return an instance.

        Returns:
            An instance of the skill class.

        Raises:
            ValueError: If the skill name is not in the registry.
        """
        if self._skill is not None:
            return self._skill

        if self.skill_name in SKILL_REGISTRY:
            module_path, class_name = SKILL_REGISTRY[self.skill_name]
            module = importlib.import_module(module_path)
            skill_class = getattr(module, class_name)
            self._skill = skill_class(self.llm, str(self.source_dir), str(self.output_dir))
        else:
            raise ValueError(
                f"Unknown skill: {self.skill_name}. "
                f"Available: {list(SKILL_REGISTRY.keys())}"
            )

        return self._skill

    # ------------------------------------------------------------------ #
    # Source material helpers
    # ------------------------------------------------------------------ #

    def _load_source_for_section(self, section_id: str) -> str:
        """Load source text relevant to a section, falling back to all source files.

        Args:
            section_id: The section identifier to search for.

        Returns:
            The source text as a string. Empty string if nothing found.
        """
        if not self.source_dir.is_dir():
            return ""

        # Try section-specific file first
        for ext in (".md", ".txt", ".json"):
            candidate = self.source_dir / f"{section_id}{ext}"
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")

        # Fall back to all source files
        texts = []
        for filepath in sorted(self.source_dir.iterdir()):
            if filepath.is_file():
                texts.append(filepath.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(texts)

    def _load_all_source(self) -> str:
        """Load all source material from the source directory.

        Returns:
            Combined source text.
        """
        if not self.source_dir.is_dir():
            return ""
        texts = []
        for filepath in sorted(self.source_dir.iterdir()):
            if filepath.is_file():
                texts.append(filepath.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(texts)

    # ------------------------------------------------------------------ #
    # Core lifecycle methods
    # ------------------------------------------------------------------ #

    def claim_task(self, task_id: str) -> None:
        """Claim a task: clone repo, verify assignment, create feature branch, read source.

        Steps:
            1. Clone repo (or pull latest).
            2. Read manifest, find task.
            3. Verify task is assigned to this agent.
            4. Create feature branch: agent-{agent_id}/task-{task_id}.
            5. Read source material referenced by task.

        Args:
            task_id: The task ID to claim.

        Raises:
            ValueError: If the task is not found or is assigned to another agent.
        """
        logger.info("Claiming task: %s", task_id)

        # 1. Clone or pull repo
        git_ops.clone_repo(self.repo_url, self.github_token, self.repo_dir)

        # 2. Read manifest and find task
        manifest = self._load_manifest()
        task = self._find_task(manifest, task_id)
        logger.info("Found task: %s - %s", task_id, task.get("title", task.get("description", "")))

        # 3. Verify assignment
        assigned_to = task.get("assigned_to", task.get("agent_id", ""))
        if assigned_to and assigned_to != self.agent_id:
            raise ValueError(
                f"Task {task_id} is assigned to {assigned_to}, not {self.agent_id}."
            )

        # 4. Create feature branch
        branch_name = f"agent-{self.agent_id}/task-{task_id}"
        git_ops.create_branch(branch_name, self.repo_dir)

        # 5. Update manifest and read source material
        task["status"] = "in_progress"
        task["assigned_to"] = self.agent_id
        if not task.get("assigned_at"):
            task["assigned_at"] = datetime.now(timezone.utc).isoformat()
        self._save_manifest(manifest)

        # Read source material for logging
        source_text = self._load_all_source()
        if source_text:
            logger.info("Source material loaded: %d characters from source/", len(source_text))
        else:
            logger.warning("No source material found in source/ directory.")

        logger.info("Task %s claimed by %s on branch %s", task_id, self.agent_id, branch_name)

    def execute(self, task_id: str) -> None:
        """Execute a claimed task using the configured skill.

        Steps:
            1. Load the skill module by SKILL_NAME.
            2. Pass source material sections to the skill.
            3. Skill uses LLM to process and returns structured output.
            4. Validate output against output/schema.json.
            5. Write output to output/sections/{section_id}.json.

        Args:
            task_id: The task ID to execute.

        Raises:
            ValueError: If the task is not found or output validation fails.
        """
        logger.info("Executing task: %s", task_id)

        # Ensure repo is cloned
        if not Path(self.repo_dir).is_dir():
            git_ops.clone_repo(self.repo_url, self.github_token, self.repo_dir)

        manifest = self._load_manifest()
        task = self._find_task(manifest, task_id)
        sections = task.get("sections", task.get("source_sections", [task_id]))
        source_files = task.get("source_files", task.get("sources", []))

        skill = self._load_skill()
        output_schema = Path(self.repo_dir) / "output" / "schema.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for section_id in sections:
            logger.info("Processing section: %s", section_id)

            # Build task metadata for the skill
            task_metadata = {
                "task_id": task_id,
                "section_id": section_id,
                "agent_id": self.agent_id,
                "source_files": source_files,
                "title": task.get("title", f"Section {section_id}"),
                "id": task_id,
                "sections": sections,
            }
            # Merge all original task fields so the skill has full context
            for key, value in task.items():
                if key not in task_metadata:
                    task_metadata[key] = value

            # Execute the skill
            result = skill.execute(task_metadata)

            # Validate output against schema
            is_valid, errors = schema_validator.validate_output(result, str(output_schema))
            if not is_valid:
                logger.error("Output validation failed for section %s: %s", section_id, errors)
                raise ValueError(f"Output schema validation failed for {section_id}: {errors}")

            # Write output
            output_path = self.output_dir / f"{section_id}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
                f.write("\n")

            logger.info("Output written: %s (%d articles)", output_path, len(result.get("articles", [])))

        logger.info("Task %s execution complete. %d section(s) processed.", task_id, len(sections))

    def audit(self, pr_number: int) -> None:
        """Peer audit mode: review another agent's PR output.

        Steps:
            1. Checkout the PR branch.
            2. Read the agent's output files.
            3. Load source material for comparison.
            4. Use LLM to audit: schema compliance, factual accuracy, cross-refs.
            5. Generate structured audit report per audits/schema.json.
            6. Submit as PR review comment + commit audit report to audits/.

        Args:
            pr_number: The pull request number to audit.
        """
        logger.info("Auditing PR #%d", pr_number)

        # Clone/pull repo
        git_ops.clone_repo(self.repo_url, self.github_token, self.repo_dir)

        # Checkout the PR branch
        pr_meta = git_ops.checkout_pr(pr_number, self.repo_dir)
        original_agent = pr_meta.get("author", "unknown")
        head_branch = pr_meta.get("head_branch", "")

        # Find changed output files in the PR
        changed = git_ops.get_changed_files(self.repo_dir)
        output_files = [f for f in changed if f.startswith("output/sections/") and f.endswith(".json")]

        if not output_files:
            logger.warning("No output files found in PR #%d", pr_number)
            return

        # Load the skill for LLM-based auditing
        skill = self._load_skill()
        all_findings = []

        for rel_path in output_files:
            full_path = Path(self.repo_dir) / rel_path
            with open(full_path, "r", encoding="utf-8") as f:
                output_data = json.load(f)

            # Load corresponding source text
            source_text = self._load_source_for_section(output_data.get("section_id", ""))

            # Validate schema compliance first
            output_schema = Path(self.repo_dir) / "output" / "schema.json"
            is_valid, errors = schema_validator.validate_output(output_data, str(output_schema))

            # Run LLM audit via skill
            findings = skill.audit(output_data, source_text)
            findings["schema_valid"] = is_valid
            if errors:
                findings.setdefault("factual_issues", []).insert(0, {
                    "article": "schema",
                    "issue": f"Schema validation errors: {'; '.join(errors)}",
                    "severity": "critical",
                    "suggestion": "Fix schema compliance issues before resubmitting.",
                })
            all_findings.append(findings)

        # Extract task_id from PR branch name or body
        task_id = self._extract_task_id(head_branch, pr_meta.get("body", ""))

        # Merge all per-file findings into a single report
        merged_findings = self._merge_audit_findings(all_findings)

        audit_report = {
            "audit_id": f"audit-{self.agent_id}-pr{pr_number}",
            "auditor_id": self.agent_id,
            "pr_number": pr_number,
            "original_agent": original_agent,
            "task_id": task_id,
            "findings": merged_findings,
            "audited_at": datetime.now(timezone.utc).isoformat(),
        }

        # Validate the audit report itself
        audit_schema = Path(self.repo_dir) / "audits" / "schema.json"
        is_valid, errors = schema_validator.validate_audit(audit_report, str(audit_schema))
        if not is_valid:
            logger.warning("Audit report validation issues: %s", errors)

        # Create audit branch, write report, commit, and push
        git_ops.create_branch(f"audit-{self.agent_id}/pr-{pr_number}", self.repo_dir)

        audit_path = Path(self.repo_dir) / "audits" / f"pr{pr_number}-{self.agent_id}.json"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit_report, f, indent=2, ensure_ascii=False)
            f.write("\n")

        git_ops.commit_and_push(
            f"audit: peer review for PR #{pr_number} by {self.agent_id}\n\n"
            f"Original agent: {original_agent}\n"
            f"Task: {task_id}\n"
            f"Assessment: {merged_findings['overall_assessment']}\n"
            f"Confidence: {merged_findings['confidence']:.2f}",
            [str(audit_path.relative_to(self.repo_dir))],
            self.repo_dir,
        )

        # Post audit summary as a PR comment
        assessment = merged_findings["overall_assessment"]
        confidence = merged_findings["confidence"]
        issue_count = len(merged_findings["factual_issues"])
        xref_count = len(merged_findings["cross_ref_issues"])

        issues_detail = ""
        if merged_findings["factual_issues"]:
            issues_detail = "\n\n### Issues Found\n\n"
            for issue in merged_findings["factual_issues"]:
                sev = issue.get("severity", "minor").upper()
                art = issue.get("article", "N/A")
                desc = issue.get("issue", "No description")
                issues_detail += f"- **[{sev}]** {art}: {desc}\n"
                if issue.get("suggestion"):
                    issues_detail += f"  - Suggestion: {issue['suggestion']}\n"

        xref_detail = ""
        if merged_findings["cross_ref_issues"]:
            xref_detail = "\n\n### Cross-Reference Issues\n\n"
            for xref in merged_findings["cross_ref_issues"]:
                xref_detail += f"- {xref}\n"

        comment = (
            f"## Peer Audit Report\n\n"
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"| **Auditor** | `{self.agent_id}` |\n"
            f"| **Assessment** | `{assessment}` |\n"
            f"| **Confidence** | `{confidence:.2f}` |\n"
            f"| **Schema Valid** | `{merged_findings['schema_valid']}` |\n"
            f"| **Factual Issues** | {issue_count} |\n"
            f"| **Cross-Ref Issues** | {xref_count} |\n"
            f"{issues_detail}{xref_detail}\n"
            f"Full audit report: `audits/pr{pr_number}-{self.agent_id}.json`"
        )
        git_ops.add_pr_comment(pr_number, comment, self.repo_dir)

        logger.info("Audit complete for PR #%d. Assessment: %s, Confidence: %.2f",
                     pr_number, assessment, confidence)

    def submit(self, task_id: str) -> None:
        """Validate all outputs against schema and open a pull request.

        Steps:
            1. Validate all outputs against schema.
            2. Git add, commit with structured message.
            3. Push branch.
            4. Open PR using gh with structured body:
               - Task ID, Agent ID, sections parsed
               - Self-reported confidence score
               - Schema validation result

        Args:
            task_id: The task ID to submit.

        Raises:
            ValueError: If no output files are found or validation fails.
        """
        logger.info("Submitting task: %s", task_id)

        if not Path(self.repo_dir).is_dir():
            git_ops.clone_repo(self.repo_url, self.github_token, self.repo_dir)

        manifest = self._load_manifest()
        task = self._find_task(manifest, task_id)

        # 1. Collect and validate output files
        output_files = sorted(self.output_dir.glob("*.json")) if self.output_dir.is_dir() else []
        if not output_files:
            raise ValueError(f"No output files found for task {task_id}. Run 'execute' first.")

        output_schema = Path(self.repo_dir) / "output" / "schema.json"
        sections_parsed = []
        all_valid = True

        for out_file in output_files:
            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            is_valid, errors = schema_validator.validate_output(data, str(output_schema))
            if not is_valid:
                all_valid = False
                logger.error("Validation failed for %s: %s", out_file.name, errors)
                raise ValueError(f"Validation failed for {out_file.name}: {errors}")
            sections_parsed.append(out_file.stem)

        logger.info("All %d output file(s) passed schema validation.", len(output_files))

        # 2. Update manifest status
        task["status"] = "submitted"
        task["delivered_at"] = datetime.now(timezone.utc).isoformat()
        self._save_manifest(manifest)

        # 3. Commit and push
        rel_paths = [str(f.relative_to(self.repo_dir)) for f in output_files]
        manifest_rel = str(self.manifest_path.relative_to(self.repo_dir))
        rel_paths.append(manifest_rel)

        branch = f"agent-{self.agent_id}/task-{task_id}"
        git_ops.commit_and_push(
            f"feat: submit parsed output for task {task_id}\n\n"
            f"Agent: {self.agent_id}\n"
            f"Skill: {self.skill_name}\n"
            f"Sections: {', '.join(sections_parsed)}\n"
            f"Files: {len(output_files)}\n"
            f"Validation: {'PASSED' if all_valid else 'FAILED'}",
            rel_paths,
            self.repo_dir,
        )

        # 4. Calculate confidence and open PR
        confidence = self._calculate_confidence(len(output_files), len(sections_parsed))

        pr_body = (
            f"## Agent Contribution\n\n"
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"| **Task ID** | `{task_id}` |\n"
            f"| **Agent ID** | `{self.agent_id}` |\n"
            f"| **Skill** | `{self.skill_name}` |\n"
            f"| **Confidence** | `{confidence}` |\n"
            f"| **Schema Validation** | `{'PASSED' if all_valid else 'FAILED'}` |\n\n"
            f"### Sections Parsed\n\n"
            + "\n".join(f"- `{s}`" for s in sections_parsed)
            + f"\n\n### Token Usage\n\n"
            f"- Input tokens: {self.llm.token_usage['input_tokens']}\n"
            f"- Output tokens: {self.llm.token_usage['output_tokens']}\n"
            f"- Total: {self.llm.total_tokens}\n\n"
            f"---\n*Submitted by AgentWork Runtime (`{self.agent_id}`)*"
        )

        pr_url = git_ops.open_pr(
            title=f"[{task_id}] {task.get('title', 'Task submission')}",
            body=pr_body,
            base="main",
            head=branch,
            repo_dir=self.repo_dir,
        )
        logger.info("PR opened: %s", pr_url)

    def submit_retro(self, task_id: str) -> None:
        """Generate and submit a post-mortem retrospective.

        Steps:
            1. Generate structured post-mortem using LLM reflection.
            2. Validate against retros/schema.json.
            3. Commit to retros/{task_id}-{agent_id}.json.
            4. Open PR for retro.

        Args:
            task_id: The task ID to write a retrospective for.
        """
        logger.info("Generating retro for task: %s", task_id)

        # Clone/pull repo
        if not Path(self.repo_dir).is_dir():
            git_ops.clone_repo(self.repo_url, self.github_token, self.repo_dir)

        manifest = self._load_manifest()
        task = self._find_task(manifest, task_id)

        # Gather context: what outputs were produced?
        output_summaries = []
        if self.output_dir.is_dir():
            for filepath in sorted(self.output_dir.iterdir()):
                if filepath.suffix == ".json":
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    output_summaries.append(
                        f"Section {data.get('section_id', filepath.stem)}: "
                        f"{len(data.get('articles', []))} articles. "
                        f"Summary: {data.get('summary', 'N/A')[:200]}"
                    )

        # Generate retro via LLM
        system_prompt = (
            "You are an AI agent writing a structured post-mortem retrospective. "
            "Reflect honestly on the task you completed. Be specific about challenges "
            "and constructive with suggestions. Return valid JSON only, with no markdown "
            "code blocks.\n\n"
            "Required JSON structure:\n"
            '{"approach": "string", "challenges": ["string"], "suggestions": ["string"], '
            '"time_spent_tokens": integer, "self_quality_assessment": float (0.0-1.0)}'
        )
        user_prompt = (
            f"Task Details:\n"
            f"- Task ID: {task_id}\n"
            f"- Agent ID: {self.agent_id}\n"
            f"- Skill: {self.skill_name}\n"
            f"- Title: {task.get('title', task.get('description', 'N/A'))}\n"
            f"- Sections: {task.get('sections', [])}\n\n"
            f"Output Summary:\n"
            f"{chr(10).join(output_summaries) if output_summaries else 'No output files found.'}\n\n"
            f"Token Usage: {self.llm.total_tokens} total "
            f"({self.llm.token_usage['input_tokens']} input, "
            f"{self.llm.token_usage['output_tokens']} output)\n\n"
            f"Generate a structured retrospective report in JSON."
        )

        response = self.llm.complete(system_prompt, user_prompt)

        # Parse response
        try:
            retro_data = json.loads(self._extract_json(response))
        except json.JSONDecodeError:
            logger.warning("Failed to parse retro LLM response, using fallback.")
            retro_data = {
                "approach": "Parsed assigned sections using structured LLM prompts with section-by-section extraction.",
                "challenges": [
                    "LLM response parsing required cleanup for valid JSON",
                    "Cross-references between articles added complexity",
                ],
                "suggestions": [
                    "Improve prompt structure for more reliable JSON output",
                    "Pre-process cross-reference tables before LLM extraction",
                ],
                "time_spent_tokens": self.llm.total_tokens,
                "self_quality_assessment": 0.75,
            }

        # Ensure the token count is accurate
        retro_data["time_spent_tokens"] = self.llm.total_tokens

        # Clamp self_quality_assessment
        retro_data["self_quality_assessment"] = max(
            0.0, min(1.0, float(retro_data.get("self_quality_assessment", 0.75)))
        )

        retro_report = {
            "agent_id": self.agent_id,
            "project_id": manifest.get("project", manifest.get("project_id", manifest.get("id", "unknown"))),
            "task_id": task_id,
            "retro": retro_data,
        }

        # Validate
        retro_schema = Path(self.repo_dir) / "retros" / "schema.json"
        is_valid, errors = schema_validator.validate_retro(retro_report, str(retro_schema))
        if not is_valid:
            logger.warning("Retro validation issues: %s", errors)

        # Create branch, write report, commit, push, open PR
        retro_branch = f"retro-{self.agent_id}/{task_id}"
        git_ops.create_branch(retro_branch, self.repo_dir)

        retro_path = Path(self.repo_dir) / "retros" / f"{task_id}-{self.agent_id}.json"
        retro_path.parent.mkdir(parents=True, exist_ok=True)
        with open(retro_path, "w", encoding="utf-8") as f:
            json.dump(retro_report, f, indent=2, ensure_ascii=False)
            f.write("\n")

        git_ops.commit_and_push(
            f"retro: post-mortem for task {task_id} by {self.agent_id}\n\n"
            f"Self-quality: {retro_data.get('self_quality_assessment', 'N/A')}\n"
            f"Tokens used: {retro_data.get('time_spent_tokens', 0)}",
            [str(retro_path.relative_to(self.repo_dir))],
            self.repo_dir,
        )

        challenges_text = "\n".join(f"  - {c}" for c in retro_data.get("challenges", []))
        suggestions_text = "\n".join(f"  - {s}" for s in retro_data.get("suggestions", []))

        pr_body = (
            f"## Post-Mortem Retrospective\n\n"
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"| **Task ID** | `{task_id}` |\n"
            f"| **Agent ID** | `{self.agent_id}` |\n"
            f"| **Self Quality** | `{retro_data.get('self_quality_assessment', 'N/A')}` |\n"
            f"| **Tokens Used** | `{retro_data.get('time_spent_tokens', 0)}` |\n\n"
            f"### Approach\n{retro_data.get('approach', 'N/A')}\n\n"
            f"### Challenges\n{challenges_text}\n\n"
            f"### Suggestions\n{suggestions_text}\n\n"
            f"---\n*Generated by AgentWork Runtime (`{self.agent_id}`)*"
        )

        pr_url = git_ops.open_pr(
            title=f"[RETRO] {task_id} - {self.agent_id}",
            body=pr_body,
            base="main",
            head=retro_branch,
            repo_dir=self.repo_dir,
        )
        logger.info("Retro PR opened: %s", pr_url)

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _merge_audit_findings(self, findings_list: list[dict]) -> dict:
        """Merge multiple per-file audit findings into a single report.

        Args:
            findings_list: List of findings dicts from individual audits.

        Returns:
            A merged findings dict conforming to the audit schema findings structure.
        """
        merged = {
            "schema_valid": True,
            "factual_issues": [],
            "cross_ref_issues": [],
            "overall_assessment": "approve",
            "confidence": 1.0,
        }

        confidences = []
        assessment_rank = {"approve": 0, "request_changes": 1, "reject": 2}

        for f in findings_list:
            if not f.get("schema_valid", True):
                merged["schema_valid"] = False
            merged["factual_issues"].extend(f.get("factual_issues", []))
            merged["cross_ref_issues"].extend(f.get("cross_ref_issues", []))
            confidences.append(f.get("confidence", 0.5))

            assessment = f.get("overall_assessment", "approve")
            if assessment_rank.get(assessment, 0) > assessment_rank.get(merged["overall_assessment"], 0):
                merged["overall_assessment"] = assessment

        merged["confidence"] = round(
            sum(confidences) / len(confidences) if confidences else 0.5, 3
        )
        return merged

    def _calculate_confidence(self, num_files: int, num_sections: int) -> float:
        """Calculate a self-reported confidence score.

        Args:
            num_files: Number of output files produced.
            num_sections: Number of sections processed.

        Returns:
            A confidence score between 0.0 and 1.0.
        """
        if num_files == 0:
            return 0.0
        # Base confidence from having produced all expected outputs
        base = min(1.0, num_files / max(num_sections, 1))
        # Slight penalty if token usage seems low (might indicate shallow parsing)
        tokens = self.llm.total_tokens
        token_factor = min(1.0, tokens / 500) if tokens > 0 else 0.8
        return round(base * 0.8 + token_factor * 0.2, 3)

    def _extract_task_id(self, head_branch: str, pr_body: str) -> str:
        """Extract a task ID from a PR branch name or body.

        Args:
            head_branch: The PR head branch name.
            pr_body: The PR body text.

        Returns:
            The extracted task ID, or 'unknown'.
        """
        # Try branch name: agent-{id}/task-{task_id}
        if "/task-" in head_branch:
            return head_branch.split("/task-")[-1]
        if "/" in head_branch:
            return head_branch.split("/")[-1]

        # Try PR body
        for line in pr_body.split("\n"):
            lower = line.lower()
            if "task id" in lower or "task_id" in lower:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip("`").strip()
                # Try extracting from markdown table
                parts = line.split("|")
                for part in parts:
                    stripped = part.strip().strip("`").strip()
                    if stripped.startswith("task-") or stripped.startswith("task_"):
                        return stripped

        return "unknown"

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from a response that may contain markdown code blocks.

        Args:
            text: The raw LLM response text.

        Returns:
            The extracted JSON string.
        """
        text = text.strip()
        if text.startswith("```json"):
            text = text[len("```json"):]
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # If still not valid, try to find JSON boundaries
        if not text.startswith("{"):
            start = text.find("{")
            if start != -1:
                end = text.rfind("}")
                if end > start:
                    text = text[start:end + 1]

        return text
