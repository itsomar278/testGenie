"""Custom exceptions for the test generator system."""


class TestGeneratorError(Exception):
    """Base exception for all test generator errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class AzureDevOpsError(TestGeneratorError):
    """Error during Azure DevOps operations."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ):
        super().__init__(
            message,
            details={"status_code": status_code, "response_body": response_body},
        )
        self.status_code = status_code
        self.response_body = response_body


class GitOperationError(TestGeneratorError):
    """Error during Git operations."""

    def __init__(
        self,
        message: str,
        command: str | None = None,
        stderr: str | None = None,
    ):
        super().__init__(
            message,
            details={"command": command, "stderr": stderr},
        )
        self.command = command
        self.stderr = stderr


class ParsingError(TestGeneratorError):
    """Error during code parsing."""

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        line_number: int | None = None,
    ):
        super().__init__(
            message,
            details={"file_path": file_path, "line_number": line_number},
        )
        self.file_path = file_path
        self.line_number = line_number


class AgentError(TestGeneratorError):
    """Error during AI agent execution."""

    def __init__(
        self,
        message: str,
        agent_name: str | None = None,
        tool_name: str | None = None,
        iteration: int | None = None,
    ):
        super().__init__(
            message,
            details={
                "agent_name": agent_name,
                "tool_name": tool_name,
                "iteration": iteration,
            },
        )
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.iteration = iteration


class BuildError(TestGeneratorError):
    """Error during .NET build."""

    def __init__(
        self,
        message: str,
        errors: list[dict] | None = None,
        warnings: list[dict] | None = None,
    ):
        super().__init__(
            message,
            details={"error_count": len(errors or []), "warning_count": len(warnings or [])},
        )
        self.errors = errors or []
        self.warnings = warnings or []


class TestExecutionError(TestGeneratorError):
    """Error during test execution."""

    def __init__(
        self,
        message: str,
        failed_tests: list[dict] | None = None,
        total_tests: int = 0,
        passed_tests: int = 0,
    ):
        super().__init__(
            message,
            details={
                "total_tests": total_tests,
                "passed_tests": passed_tests,
                "failed_count": len(failed_tests or []),
            },
        )
        self.failed_tests = failed_tests or []
        self.total_tests = total_tests
        self.passed_tests = passed_tests


class ToolExecutionError(TestGeneratorError):
    """Error during tool execution by an agent."""

    def __init__(
        self,
        message: str,
        tool_name: str,
        arguments: dict | None = None,
    ):
        super().__init__(
            message,
            details={"tool_name": tool_name, "arguments": arguments},
        )
        self.tool_name = tool_name
        self.arguments = arguments or {}


class ContextOverflowError(TestGeneratorError):
    """Error when context window is exceeded."""

    def __init__(
        self,
        message: str,
        current_tokens: int,
        max_tokens: int,
    ):
        super().__init__(
            message,
            details={"current_tokens": current_tokens, "max_tokens": max_tokens},
        )
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens
