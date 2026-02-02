"""Test generator agent for creating and updating xUnit tests."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotnet_test_generator.agents.base import BaseAgent, AgentConfig
from dotnet_test_generator.agents.ollama_client import OllamaClient, ChatResponse
from dotnet_test_generator.agents.prompts.test_generation import TestGenerationPrompts
from dotnet_test_generator.agents.tools.base import BaseTool
from dotnet_test_generator.agents.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    ListDirectoryTool,
    SearchFilesTool,
)
from dotnet_test_generator.agents.tools.dotnet_tools import (
    DotnetBuildTool,
    DotnetTestTool,
)
from dotnet_test_generator.parsing.change_detector import ChangeContext
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TestGenerationResult:
    """Result of test generation for a single file."""

    source_path: str
    test_path: str
    action: str  # created, updated, deleted, skipped
    tests_written: int
    success: bool
    error: str | None = None


class TestGeneratorAgent(BaseAgent):
    """
    Agent for generating and updating xUnit tests.

    Handles test creation for new files, updates for modified files,
    and deletion for removed files.
    """

    def __init__(
        self,
        client: OllamaClient,
        repo_path: Path,
        config: AgentConfig | None = None,
    ):
        """
        Initialize test generator agent.

        Args:
            client: Ollama client for LLM inference
            repo_path: Path to the repository root
            config: Agent configuration (uses defaults if not provided)
        """
        self.repo_path = repo_path

        if config is None:
            config = AgentConfig(
                name="TestGenerator",
                max_iterations=30,
                max_context_tokens=75000,
            )

        # Create tools
        tools: list[BaseTool] = [
            ReadFileTool(repo_path),
            WriteFileTool(repo_path),
            ListDirectoryTool(repo_path),
            SearchFilesTool(repo_path),
            DotnetBuildTool(repo_path),
            DotnetTestTool(repo_path),
        ]

        super().__init__(client, config, tools)

        self._current_context: ChangeContext | None = None
        self._test_path: str = ""

    def get_system_prompt(self) -> str:
        """Get the system prompt for test generation."""
        return TestGenerationPrompts.SYSTEM_PROMPT

    def get_initial_user_message(self, **kwargs) -> str:
        """
        Get the initial user message.

        Expected kwargs:
            context: ChangeContext with source and test file info
        """
        context: ChangeContext = kwargs["context"]
        self._current_context = context
        self._test_path = context.test_file_path or ""

        return TestGenerationPrompts.get_test_update_prompt(
            source_file_old=context.source_content_old,
            source_file_new=context.source_content_new or "",
            test_file_current=context.test_content_current,
            source_path=context.change.path,
            test_path=self._test_path,
        )

    def process_result(self, response: ChatResponse) -> TestGenerationResult:
        """Process the final response and create result."""
        if not self._current_context:
            return TestGenerationResult(
                source_path="",
                test_path="",
                action="skipped",
                tests_written=0,
                success=False,
                error="No context available",
            )

        # Check if test file was written
        test_file = self.repo_path / self._test_path
        if test_file.exists():
            # Count test methods (rough estimate)
            try:
                content = test_file.read_text()
                fact_count = content.count("[Fact]")
                theory_count = content.count("[Theory]")
                tests_written = fact_count + theory_count
            except Exception:
                tests_written = 0

            action = "updated" if self._current_context.test_content_current else "created"

            return TestGenerationResult(
                source_path=self._current_context.change.path,
                test_path=self._test_path,
                action=action,
                tests_written=tests_written,
                success=True,
            )
        else:
            return TestGenerationResult(
                source_path=self._current_context.change.path,
                test_path=self._test_path,
                action="skipped",
                tests_written=0,
                success=False,
                error="Test file was not created",
            )

    def _is_task_complete(self, response: ChatResponse) -> bool:
        """Check if test generation is complete."""
        # Check if test file exists
        if self._test_path:
            test_file = self.repo_path / self._test_path
            if test_file.exists():
                return True

        # Check response content for completion markers
        content = response.content.lower()
        completion_markers = [
            "test file has been created",
            "test file has been written",
            "tests have been generated",
            "successfully wrote",
            "tests written to",
        ]
        return any(marker in content for marker in completion_markers)

    def _get_continuation_prompt(self, response: ChatResponse) -> str | None:
        """Get continuation prompt if task is not complete."""
        if self._test_path:
            test_file = self.repo_path / self._test_path
            if not test_file.exists():
                return TestGenerationPrompts.get_continuation_prompt()
        return None

    def generate_tests_for_context(
        self,
        context: ChangeContext,
    ) -> TestGenerationResult:
        """
        Generate tests for a change context.

        Args:
            context: Change context with source and test file info

        Returns:
            TestGenerationResult with outcome
        """
        logger.info(f"Generating tests for: {context.change.path}")

        try:
            result = self.run(context=context)
            return result

        except Exception as e:
            logger.error(f"Test generation failed: {e}")
            return TestGenerationResult(
                source_path=context.change.path,
                test_path=context.test_file_path or "",
                action="error",
                tests_written=0,
                success=False,
                error=str(e),
            )


class TestGeneratorOrchestrator:
    """
    Orchestrates test generation for multiple files.

    Manages the overall test generation process for a pull request.
    """

    def __init__(
        self,
        client: OllamaClient,
        repo_path: Path,
    ):
        """
        Initialize orchestrator.

        Args:
            client: Ollama client for LLM inference
            repo_path: Path to repository root
        """
        self.client = client
        self.repo_path = repo_path

    def generate_tests_for_changes(
        self,
        contexts: list[ChangeContext],
    ) -> list[TestGenerationResult]:
        """
        Generate tests for all changed files.

        Args:
            contexts: List of change contexts

        Returns:
            List of generation results
        """
        results = []

        for i, context in enumerate(contexts):
            logger.info(f"Processing file {i + 1}/{len(contexts)}: {context.change.path}")

            agent = TestGeneratorAgent(
                client=self.client,
                repo_path=self.repo_path,
            )

            result = agent.generate_tests_for_context(context)
            results.append(result)

            logger.info(
                f"Result: {result.action} - "
                f"{result.tests_written} tests - "
                f"Success: {result.success}"
            )

        return results

    def get_summary(self, results: list[TestGenerationResult]) -> dict:
        """
        Get summary of test generation results.

        Args:
            results: List of generation results

        Returns:
            Summary dictionary
        """
        return {
            "total_files": len(results),
            "created": len([r for r in results if r.action == "created"]),
            "updated": len([r for r in results if r.action == "updated"]),
            "deleted": len([r for r in results if r.action == "deleted"]),
            "skipped": len([r for r in results if r.action == "skipped"]),
            "errors": len([r for r in results if r.action == "error"]),
            "total_tests_written": sum(r.tests_written for r in results),
            "success_rate": (
                len([r for r in results if r.success]) / len(results)
                if results else 0
            ),
        }
