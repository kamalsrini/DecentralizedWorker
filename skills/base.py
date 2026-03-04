from __future__ import annotations

"""Base skill interface for all AgentWork skills.

Every skill module must subclass BaseSkill and implement the execute()
and audit() methods. Skills receive an LLM client, source directory,
and output directory at construction time.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Union

from agent_worker.llm import LLMClient

logger = logging.getLogger(__name__)


class BaseSkill(ABC):
    """Abstract base class for all agent skills.

    Skills are pluggable modules that define how an agent processes source
    material and produces structured output. Each skill also knows how to
    audit another agent's output for the same task type.

    Subclasses must implement:
        - execute(task_metadata) -> dict: Process source material and return structured output.
        - audit(output_data, source_data) -> dict: Audit another agent's output against source.
    """

    def __init__(self, llm_client: LLMClient, source_dir: Union[str, Path], output_dir: Union[str, Path]):
        """Initialize the skill with shared resources.

        Args:
            llm_client: An initialized LLMClient for making LLM calls.
            source_dir: Path to the directory containing source material.
            output_dir: Path to the directory where output should be written.
        """
        self.llm = llm_client
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)
        logger.info(
            "Initialized skill %s (source=%s, output=%s)",
            self.__class__.__name__,
            self.source_dir,
            self.output_dir,
        )

    def read_source_file(self, filename: str) -> str:
        """Read a source file and return its contents.

        Args:
            filename: Name of the file within source_dir.

        Returns:
            The file contents as a string.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        filepath = self.source_dir / filename
        if not filepath.is_file():
            raise FileNotFoundError(f"Source file not found: {filepath}")
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    def list_source_files(self) -> list[str]:
        """List all files in the source directory.

        Returns:
            A sorted list of filenames in the source directory.
        """
        if not self.source_dir.is_dir():
            logger.warning("Source directory does not exist: %s", self.source_dir)
            return []
        return sorted(f.name for f in self.source_dir.iterdir() if f.is_file())

    @abstractmethod
    def execute(self, task_metadata: dict) -> dict:
        """Execute the skill against assigned source material.

        Subclasses must override this method to implement their specific
        parsing or processing logic.

        Args:
            task_metadata: A dict containing task details such as:
                - task_id: The task identifier.
                - section_id: The section being parsed.
                - agent_id: The agent performing the task.
                - source_files: List of source filenames to process.

        Returns:
            A dict of structured output conforming to output/schema.json.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement execute()")

    @abstractmethod
    def audit(self, output_data: dict, source_data: str) -> dict:
        """Audit another agent's output by comparing it against source material.

        Subclasses must override this method to implement their specific
        audit logic.

        Args:
            output_data: The structured output produced by the original agent.
            source_data: The raw source material text for comparison.

        Returns:
            A dict of audit findings conforming to audits/schema.json findings structure.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement audit()")
