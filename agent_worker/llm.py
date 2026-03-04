from __future__ import annotations

"""LLM provider abstraction layer.

Supports Anthropic (Claude), OpenAI, and a local/mock provider for testing.
The LLMClient class provides a unified interface for all providers.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM client supporting multiple providers.

    Providers:
        - "anthropic": Uses the Anthropic Python SDK (Claude models).
        - "openai": Uses the OpenAI Python SDK (GPT models).
        - "local": A mock provider for testing that returns structured JSON stubs.

    The API key is read from the LLM_API_KEY environment variable.
    """

    SUPPORTED_PROVIDERS = ("anthropic", "openai", "local")

    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None):
        """Initialize the LLM client.

        Args:
            provider: One of 'anthropic', 'openai', or 'local'.
                      Defaults to the LLM_PROVIDER environment variable, then 'anthropic'.
            api_key: The API key. Defaults to the LLM_API_KEY environment variable.

        Raises:
            ValueError: If the provider is not supported.
            ValueError: If a non-local provider is selected but no API key is available.
        """
        self.provider = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self._client = None
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        if self.provider not in self.SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported LLM provider '{self.provider}'. "
                f"Supported: {', '.join(self.SUPPORTED_PROVIDERS)}"
            )

        if self.provider != "local" and not self.api_key:
            raise ValueError(
                f"LLM_API_KEY environment variable is required for provider '{self.provider}'."
            )

        self._init_client()

    def _init_client(self) -> None:
        """Initialize the provider-specific client."""
        if self.provider == "anthropic":
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
                logger.info("Initialized Anthropic client.")
            except ImportError:
                raise ImportError("The 'anthropic' package is required. Install it with: pip install anthropic")

        elif self.provider == "openai":
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.api_key)
                logger.info("Initialized OpenAI client.")
            except ImportError:
                raise ImportError("The 'openai' package is required. Install it with: pip install openai")

        elif self.provider == "local":
            logger.info("Using local/mock LLM provider for testing.")
            self._client = None

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt to the LLM and return the response text.

        Args:
            system_prompt: The system-level instruction for the LLM.
            user_prompt: The user-level prompt or content to process.

        Returns:
            The LLM's response as a string.

        Raises:
            RuntimeError: If the API call fails after retries.
        """
        logger.debug("LLM complete() called. Provider=%s, system_prompt length=%d, user_prompt length=%d",
                      self.provider, len(system_prompt), len(user_prompt))

        if self.provider == "anthropic":
            return self._complete_anthropic(system_prompt, user_prompt)
        elif self.provider == "openai":
            return self._complete_openai(system_prompt, user_prompt)
        elif self.provider == "local":
            return self._complete_local(system_prompt, user_prompt)
        else:
            raise RuntimeError(f"No completion handler for provider: {self.provider}")

    def _complete_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        """Call Anthropic Claude API.

        Uses claude-sonnet-4-20250514 as the default model with a 4096 max token output.
        """
        try:
            response = self._client.messages.create(
                model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                max_tokens=int(os.environ.get("MAX_TOKENS", "8192")),
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ],
            )
            # Track token usage
            if hasattr(response, "usage"):
                self._total_input_tokens += response.usage.input_tokens
                self._total_output_tokens += response.usage.output_tokens
                logger.debug("Tokens used: input=%d, output=%d",
                             response.usage.input_tokens, response.usage.output_tokens)

            text = response.content[0].text
            return text

        except Exception as exc:
            logger.error("Anthropic API call failed: %s", exc)
            raise RuntimeError(f"Anthropic API call failed: {exc}") from exc

    def _complete_openai(self, system_prompt: str, user_prompt: str) -> str:
        """Call OpenAI ChatCompletion API.

        Uses gpt-4o as the default model with a 4096 max token output.
        """
        try:
            response = self._client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            # Track token usage
            if response.usage:
                self._total_input_tokens += response.usage.prompt_tokens
                self._total_output_tokens += response.usage.completion_tokens
                logger.debug("Tokens used: input=%d, output=%d",
                             response.usage.prompt_tokens, response.usage.completion_tokens)

            text = response.choices[0].message.content
            return text

        except Exception as exc:
            logger.error("OpenAI API call failed: %s", exc)
            raise RuntimeError(f"OpenAI API call failed: {exc}") from exc

    def _complete_local(self, system_prompt: str, user_prompt: str) -> str:
        """Local mock provider for testing without an API key.

        Returns a structured JSON stub based on keyword detection in the prompts.
        This allows the full pipeline to be tested locally without incurring API costs.
        """
        logger.info("Local provider: generating mock response.")
        self._total_input_tokens += len(system_prompt.split()) + len(user_prompt.split())
        self._total_output_tokens += 50

        combined = (system_prompt + " " + user_prompt).lower()

        if "audit" in combined:
            return json.dumps({
                "schema_valid": True,
                "factual_issues": [],
                "cross_ref_issues": [],
                "overall_assessment": "approve",
                "confidence": 0.85,
            }, indent=2)

        if "retro" in combined or "post-mortem" in combined:
            return json.dumps({
                "approach": "Parsed source text using structured prompts with section-by-section extraction.",
                "challenges": [
                    "Ambiguous cross-references between articles",
                    "Nested obligation structures required multi-pass parsing",
                ],
                "suggestions": [
                    "Provide clearer section boundaries in source material",
                    "Pre-process cross-reference tables for faster lookup",
                ],
                "time_spent_tokens": self._total_input_tokens + self._total_output_tokens,
                "self_quality_assessment": 0.8,
            }, indent=2)

        # Default: section parsing response
        return json.dumps({
            "articles": [
                {
                    "article_number": "1",
                    "title": "Mock Article Title",
                    "text": "This is a mock article text for local testing.",
                    "obligations": ["Mock obligation 1"],
                    "applies_to": ["AI providers"],
                    "risk_category": "high",
                    "cross_references": ["Article 2"],
                    "key_definitions": [
                        {"term": "AI system", "definition": "A mock definition for testing."}
                    ],
                }
            ],
            "summary": "Mock summary generated by local provider for testing.",
        }, indent=2)

    @property
    def total_tokens(self) -> int:
        """Return the total number of tokens consumed across all calls."""
        return self._total_input_tokens + self._total_output_tokens

    @property
    def token_usage(self) -> dict:
        """Return a dict with input_tokens, output_tokens, and total."""
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total": self._total_input_tokens + self._total_output_tokens,
        }
