"""Dotnet CLI tools for agents."""

import subprocess
import re
from pathlib import Path
from dataclasses import dataclass, field

from dotnet_test_generator.agents.tools.base import BaseTool, ToolResult
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BuildError:
    """Parsed build error."""

    file: str
    line: int
    column: int
    code: str
    message: str
    severity: str = "error"


@dataclass
class TestResult:
    """Individual test result."""

    name: str
    outcome: str  # Passed, Failed, Skipped
    duration_ms: int = 0
    error_message: str | None = None
    stack_trace: str | None = None


class DotnetRestoreTool(BaseTool):
    """Tool to run dotnet restore."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    @property
    def name(self) -> str:
        return "dotnet_restore"

    @property
    def description(self) -> str:
        return (
            "Restore NuGet packages for the solution. "
            "Should be run before building if packages are missing."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional project or solution file path",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        project = kwargs.get("project")

        cmd = ["dotnet", "restore"]
        if project:
            cmd.append(project)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                return ToolResult(
                    success=True,
                    output="Restore completed successfully\n" + result.stdout,
                )
            else:
                return ToolResult(
                    success=False,
                    output=result.stdout,
                    error=result.stderr,
                )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output="",
                error="Restore timed out after 5 minutes",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class DotnetBuildTool(BaseTool):
    """Tool to build .NET solution."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    @property
    def name(self) -> str:
        return "dotnet_build"

    @property
    def description(self) -> str:
        return (
            "Build the .NET solution. Returns build errors if any. "
            "Use this to verify that code changes compile correctly."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional project or solution file path",
                },
                "configuration": {
                    "type": "string",
                    "description": "Build configuration (Debug or Release)",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        project = kwargs.get("project")
        configuration = kwargs.get("configuration", "Debug")

        cmd = ["dotnet", "build", "--no-restore", "-c", configuration]
        if project:
            cmd.append(project)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=600,
            )

            errors = self._parse_build_errors(result.stdout + result.stderr)

            if result.returncode == 0:
                return ToolResult(
                    success=True,
                    output="Build succeeded\n" + result.stdout,
                    data={"errors": [], "warnings": len([e for e in errors if e.severity == "warning"])},
                )
            else:
                error_output = self._format_errors(errors)
                return ToolResult(
                    success=False,
                    output=error_output,
                    error=f"Build failed with {len([e for e in errors if e.severity == 'error'])} errors",
                    data={"errors": [self._error_to_dict(e) for e in errors]},
                )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output="",
                error="Build timed out after 10 minutes",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _parse_build_errors(self, output: str) -> list[BuildError]:
        """Parse MSBuild error output."""
        errors = []
        # Pattern: file(line,col): error/warning CODE: message
        pattern = r"([^(]+)\((\d+),(\d+)\):\s*(error|warning)\s+(\w+):\s*(.+)"

        for line in output.split("\n"):
            match = re.search(pattern, line)
            if match:
                errors.append(BuildError(
                    file=match.group(1).strip(),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    severity=match.group(4),
                    code=match.group(5),
                    message=match.group(6).strip(),
                ))

        return errors

    def _format_errors(self, errors: list[BuildError]) -> str:
        """Format errors for display."""
        lines = []
        for error in errors:
            lines.append(
                f"[{error.severity.upper()}] {error.file}({error.line},{error.column}): "
                f"{error.code}: {error.message}"
            )
        return "\n".join(lines)

    def _error_to_dict(self, error: BuildError) -> dict:
        return {
            "file": error.file,
            "line": error.line,
            "column": error.column,
            "code": error.code,
            "message": error.message,
            "severity": error.severity,
        }


class DotnetTestTool(BaseTool):
    """Tool to run .NET tests."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    @property
    def name(self) -> str:
        return "dotnet_test"

    @property
    def description(self) -> str:
        return (
            "Run xUnit tests. Returns test results including passed, failed, and skipped counts. "
            "Use this to verify that tests pass after making changes."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional test project path",
                },
                "filter": {
                    "type": "string",
                    "description": "Test filter expression (e.g., 'FullyQualifiedName~MyTest')",
                },
                "no_build": {
                    "type": "boolean",
                    "description": "Skip build before running tests (default: true)",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        project = kwargs.get("project")
        filter_expr = kwargs.get("filter")
        no_build = kwargs.get("no_build", True)

        cmd = ["dotnet", "test", "--logger", "console;verbosity=detailed"]

        if no_build:
            cmd.append("--no-build")

        if filter_expr:
            cmd.extend(["--filter", filter_expr])

        if project:
            cmd.append(project)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=600,
            )

            summary = self._parse_test_summary(result.stdout)
            failed_tests = self._parse_failed_tests(result.stdout + result.stderr)

            output_lines = [
                f"Total: {summary.get('total', 0)}",
                f"Passed: {summary.get('passed', 0)}",
                f"Failed: {summary.get('failed', 0)}",
                f"Skipped: {summary.get('skipped', 0)}",
            ]

            if failed_tests:
                output_lines.append("\nFailed Tests:")
                for test in failed_tests[:10]:  # Limit output
                    output_lines.append(f"  - {test['name']}")
                    if test.get('error'):
                        output_lines.append(f"    Error: {test['error'][:200]}")

            if result.returncode == 0:
                return ToolResult(
                    success=True,
                    output="\n".join(output_lines),
                    data={"summary": summary, "failed_tests": failed_tests},
                )
            else:
                return ToolResult(
                    success=False,
                    output="\n".join(output_lines),
                    error=f"{summary.get('failed', 0)} tests failed",
                    data={"summary": summary, "failed_tests": failed_tests},
                )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output="",
                error="Tests timed out after 10 minutes",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _parse_test_summary(self, output: str) -> dict:
        """Parse test summary from output."""
        summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

        # Look for summary line: "Passed!  - Failed: 0, Passed: 5, Skipped: 0, Total: 5"
        # Or: "Failed!  - Failed: 1, Passed: 4, Skipped: 0, Total: 5"
        patterns = {
            "total": r"Total:\s*(\d+)",
            "passed": r"Passed:\s*(\d+)",
            "failed": r"Failed:\s*(\d+)",
            "skipped": r"Skipped:\s*(\d+)",
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, output)
            if match:
                summary[key] = int(match.group(1))

        return summary

    def _parse_failed_tests(self, output: str) -> list[dict]:
        """Parse failed test details."""
        failed = []

        # Look for failed test entries
        # Pattern varies but typically: "Failed TestName [duration]"
        current_test = None
        current_error = []

        for line in output.split("\n"):
            if "Failed " in line and "[" in line:
                if current_test:
                    failed.append({
                        "name": current_test,
                        "error": "\n".join(current_error).strip(),
                    })

                # Extract test name
                match = re.search(r"Failed\s+(.+?)\s*\[", line)
                if match:
                    current_test = match.group(1)
                    current_error = []

            elif current_test and line.strip():
                current_error.append(line)

        # Don't forget the last one
        if current_test:
            failed.append({
                "name": current_test,
                "error": "\n".join(current_error).strip(),
            })

        return failed


class DotnetCleanTool(BaseTool):
    """Tool to clean build outputs."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    @property
    def name(self) -> str:
        return "dotnet_clean"

    @property
    def description(self) -> str:
        return "Clean build outputs. Use this if builds are failing due to stale artifacts."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional project or solution file path",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        project = kwargs.get("project")

        cmd = ["dotnet", "clean"]
        if project:
            cmd.append(project)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode == 0:
                return ToolResult(
                    success=True,
                    output="Clean completed successfully",
                )
            else:
                return ToolResult(
                    success=False,
                    output=result.stdout,
                    error=result.stderr,
                )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
