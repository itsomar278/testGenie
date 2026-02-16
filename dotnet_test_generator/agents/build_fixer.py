"""Build fixer agent for resolving compilation errors."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotnet_test_generator.agents.base import BaseAgent, AgentConfig
from dotnet_test_generator.agents.ollama_client import OllamaClient, ChatResponse
from dotnet_test_generator.agents.prompts.build_fixing import BuildFixingPrompts
from dotnet_test_generator.agents.tools.base import BaseTool
from dotnet_test_generator.agents.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    ListDirectoryTool,
    SearchFilesTool,
)
from dotnet_test_generator.agents.tools.dotnet_tools import (
    DotnetBuildTool,
    DotnetRestoreTool,
    DotnetCleanTool,
)
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BuildFixResult:
    """Result of build fix attempt."""

    success: bool
    iterations_used: int
    errors_fixed: int
    remaining_errors: list[dict] = field(default_factory=list)
    error: str | None = None


class BuildFixerAgent(BaseAgent):
    """
    Agent for fixing build errors.

    Iteratively analyzes and fixes compilation errors until the build succeeds.
    """

    def __init__(
        self,
        client: OllamaClient,
        repo_path: Path,
        config: AgentConfig | None = None,
    ):
        """
        Initialize build fixer agent.

        Args:
            client: Ollama client for LLM inference
            repo_path: Path to the repository root
            config: Agent configuration (uses defaults if not provided)
        """
        self.repo_path = repo_path

        if config is None:
            config = AgentConfig(
                name="BuildFixer",
                max_iterations=40,
                max_context_tokens=60000,
            )

        # Create tools
        tools: list[BaseTool] = [
            ReadFileTool(repo_path),
            WriteFileTool(repo_path),
            ListDirectoryTool(repo_path),
            SearchFilesTool(repo_path),
            DotnetBuildTool(repo_path),
            DotnetRestoreTool(repo_path),
            DotnetCleanTool(repo_path),
        ]

        super().__init__(client, config, tools)

        self._initial_errors: list[dict] = []
        self._current_errors: list[dict] = []
        self._build_iteration = 0

    def get_system_prompt(self) -> str:
        """Get the system prompt for build fixing."""
        return BuildFixingPrompts.SYSTEM_PROMPT

    def get_initial_user_message(self, **kwargs) -> str:
        """
        Get the initial user message.

        Expected kwargs:
            errors: List of build error dictionaries
        """
        self._initial_errors = kwargs.get("errors", [])
        self._current_errors = self._initial_errors.copy()
        self._build_iteration = 1

        return BuildFixingPrompts.get_build_error_prompt(
            errors=self._current_errors,
            iteration=self._build_iteration,
            max_iterations=self.config.max_iterations,
        )

    def process_result(self, response: ChatResponse) -> BuildFixResult:
        """Process the final response and create result."""
        errors_fixed = len(self._initial_errors) - len(self._current_errors)

        return BuildFixResult(
            success=len(self._current_errors) == 0,
            iterations_used=self._build_iteration,
            errors_fixed=errors_fixed,
            remaining_errors=self._current_errors,
        )

    def _is_task_complete(self, response: ChatResponse) -> bool:
        """Check if build fixing is complete."""
        content = response.content.lower()

        # Check for success indicators
        if "build successful" in content or "build fix complete" in content:
            return True

        # Check if no errors remain
        if len(self._current_errors) == 0:
            return True

        return False

    def _get_continuation_prompt(self, response: ChatResponse) -> str | None:
        """Get continuation prompt if errors remain."""
        if self._current_errors:
            self._build_iteration += 1

            remaining = self.config.max_iterations - self.state.iteration
            if remaining <= 3:
                return BuildFixingPrompts.get_iteration_limit_prompt(remaining)

            return BuildFixingPrompts.get_build_error_prompt(
                errors=self._current_errors,
                iteration=self._build_iteration,
                max_iterations=self.config.max_iterations,
            )

        return BuildFixingPrompts.get_success_prompt()


class BuildFixOrchestrator:
    """
    Orchestrates the build fix process.

    Runs the build fixer agent iteratively until the build succeeds
    or max iterations is reached.
    """

    def __init__(
        self,
        client: OllamaClient,
        repo_path: Path,
        max_iterations: int = 10,
    ):
        """
        Initialize orchestrator.

        Args:
            client: Ollama client for LLM inference
            repo_path: Path to repository root
            max_iterations: Maximum fix iterations
        """
        self.client = client
        self.repo_path = repo_path
        self.max_iterations = max_iterations

    def fix_build(self, initial_errors: list[dict] | None = None) -> BuildFixResult:
        """
        Attempt to fix build errors.

        Args:
            initial_errors: Optional list of known errors (will run build if not provided)

        Returns:
            BuildFixResult with outcome
        """
        logger.info("Starting build fix process")

        # Get initial errors if not provided
        if initial_errors is None:
            initial_errors = self._run_build()

        if not initial_errors:
            logger.info("No build errors to fix")
            return BuildFixResult(
                success=True,
                iterations_used=0,
                errors_fixed=0,
            )

        logger.info(f"Found {len(initial_errors)} build errors")

        # Run the agent
        config = AgentConfig(
            name="BuildFixer",
            max_iterations=self.max_iterations * 3,  # More iterations for complex fixes
            max_context_tokens=60000,
        )

        agent = BuildFixerAgent(
            client=self.client,
            repo_path=self.repo_path,
            config=config,
        )

        try:
            result = agent.run(errors=initial_errors)

            # Handle case where agent returns None (e.g. max iterations hit)
            if result is None:
                logger.warning("Build fixer agent returned no result")
                result = BuildFixResult(
                    success=False,
                    iterations_used=agent.state.iteration,
                    errors_fixed=0,
                    remaining_errors=initial_errors,
                    error="Agent did not produce a result (max iterations or no tool calls)",
                )

            # Verify with a final build
            final_errors = self._run_build()
            result.remaining_errors = final_errors
            result.success = len(final_errors) == 0

            return result

        except Exception as e:
            logger.error(f"Build fix failed: {e}")
            return BuildFixResult(
                success=False,
                iterations_used=0,
                errors_fixed=0,
                remaining_errors=initial_errors or [],
                error=str(e),
            )

    def _run_build(self) -> list[dict]:
        """Run dotnet build and return errors."""
        import subprocess
        import re

        try:
            result = subprocess.run(
                ["dotnet", "build", "--no-restore"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                return []

            # Parse errors
            errors = []
            pattern = r"([^(]+)\((\d+),(\d+)\):\s*(error)\s+(\w+):\s*(.+)"

            for line in (result.stdout + result.stderr).split("\n"):
                match = re.search(pattern, line)
                if match:
                    errors.append({
                        "file": match.group(1).strip(),
                        "line": int(match.group(2)),
                        "column": int(match.group(3)),
                        "severity": match.group(4),
                        "code": match.group(5),
                        "message": match.group(6).strip(),
                    })

            return errors

        except Exception as e:
            logger.error(f"Build failed: {e}")
            return [{"file": "unknown", "line": 0, "column": 0, "code": "BUILD", "message": str(e)}]


class TestFixerAgent(BaseAgent):
    """
    Agent for fixing failing tests.

    Analyzes test failures and determines whether to fix tests or source code.
    """

    def __init__(
        self,
        client: OllamaClient,
        repo_path: Path,
        config: AgentConfig | None = None,
    ):
        """
        Initialize test fixer agent.

        Args:
            client: Ollama client for LLM inference
            repo_path: Path to the repository root
            config: Agent configuration
        """
        self.repo_path = repo_path

        if config is None:
            config = AgentConfig(
                name="TestFixer",
                max_iterations=30,
                max_context_tokens=60000,
            )

        tools: list[BaseTool] = [
            ReadFileTool(repo_path),
            WriteFileTool(repo_path),
            ListDirectoryTool(repo_path),
            SearchFilesTool(repo_path),
            DotnetBuildTool(repo_path),
        ]

        super().__init__(client, config, tools)

        self._test_failures: list[dict] = []
        self._fix_iteration = 0

    def get_system_prompt(self) -> str:
        """Get the system prompt for test fixing."""
        return BuildFixingPrompts.SYSTEM_PROMPT + """

## Additional Context for Test Fixing

When analyzing test failures, determine the root cause:

1. **Test is wrong**: The test expectation doesn't match intended behavior
   - Fix the test assertions
   - Update test data

2. **Source code is wrong**: The production code has a bug
   - Fix the source code
   - Don't change the test

3. **Test setup is wrong**: The test arrangement is incorrect
   - Fix the test setup/mocking
   - Ensure proper initialization

Always explain your reasoning before making changes."""

    def get_initial_user_message(self, **kwargs) -> str:
        """Get the initial user message."""
        self._test_failures = kwargs.get("failures", [])
        self._fix_iteration = 1

        return BuildFixingPrompts.get_test_failure_fix_prompt(
            test_failures=self._test_failures,
            iteration=self._fix_iteration,
        )

    def process_result(self, response: ChatResponse) -> dict:
        """Process result."""
        return {
            "iterations": self._fix_iteration,
            "initial_failures": len(self._test_failures),
        }

    def _is_task_complete(self, response: ChatResponse) -> bool:
        """Check if test fixing is complete."""
        content = response.content.lower()
        return "tests pass" in content or "all tests pass" in content

    def _get_continuation_prompt(self, response: ChatResponse) -> str | None:
        """Get continuation if needed."""
        self._fix_iteration += 1
        if self._fix_iteration <= 5:
            return "Please verify the fix by running the tests with dotnet_test."
        return None
