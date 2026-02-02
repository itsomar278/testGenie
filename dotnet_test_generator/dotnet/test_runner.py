"""Test execution for .NET projects."""

import subprocess
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TestCaseResult:
    """Result of a single test case."""

    name: str
    class_name: str
    outcome: str  # Passed, Failed, Skipped
    duration_ms: float
    error_message: str | None = None
    stack_trace: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.class_name}.{self.name}"

    @property
    def passed(self) -> bool:
        return self.outcome == "Passed"

    @property
    def failed(self) -> bool:
        return self.outcome == "Failed"


@dataclass
class TestRunResult:
    """Result of a test run."""

    total: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    test_cases: list[TestCaseResult] = field(default_factory=list)
    output: str = ""
    success: bool = True

    @property
    def failed_tests(self) -> list[TestCaseResult]:
        return [t for t in self.test_cases if t.failed]

    @property
    def passed_tests(self) -> list[TestCaseResult]:
        return [t for t in self.test_cases if t.passed]


class TestRunner:
    """
    Handles test execution for .NET projects.

    Uses dotnet test to run xUnit tests and parses results.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize test runner.

        Args:
            repo_path: Path to repository root
        """
        self.repo_path = repo_path
        self.results_path = repo_path / "TestResults"

    def run_tests(
        self,
        project: str | None = None,
        filter_expr: str | None = None,
        no_build: bool = True,
        timeout: int = 600,
        collect_coverage: bool = False,
    ) -> TestRunResult:
        """
        Run tests.

        Args:
            project: Optional test project path
            filter_expr: Test filter expression
            no_build: Skip build step
            timeout: Timeout in seconds
            collect_coverage: Collect code coverage

        Returns:
            TestRunResult with execution details
        """
        logger.info("Running tests")

        import time
        start_time = time.time()

        # Prepare command
        cmd = [
            "dotnet", "test",
            "--logger", "trx",
            "--logger", "console;verbosity=normal",
            "--results-directory", str(self.results_path),
        ]

        if no_build:
            cmd.append("--no-build")

        if filter_expr:
            cmd.extend(["--filter", filter_expr])

        if collect_coverage:
            cmd.extend(["--collect", "XPlat Code Coverage"])

        if project:
            cmd.append(project)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration = time.time() - start_time
            output = result.stdout + result.stderr

            # Parse results from console output
            test_result = self._parse_console_output(output)
            test_result.duration_seconds = duration
            test_result.output = output
            test_result.success = result.returncode == 0

            # Try to get detailed results from TRX file
            trx_results = self._parse_trx_results()
            if trx_results:
                test_result.test_cases = trx_results

            return test_result

        except subprocess.TimeoutExpired:
            return TestRunResult(
                total=0,
                passed=0,
                failed=0,
                skipped=0,
                duration_seconds=timeout,
                output="Test run timed out",
                success=False,
            )
        except Exception as e:
            return TestRunResult(
                total=0,
                passed=0,
                failed=0,
                skipped=0,
                duration_seconds=0,
                output=str(e),
                success=False,
            )

    def _parse_console_output(self, output: str) -> TestRunResult:
        """Parse test results from console output."""
        total = passed = failed = skipped = 0

        # Look for summary line
        patterns = {
            "total": r"Total:\s*(\d+)",
            "passed": r"Passed:\s*(\d+)",
            "failed": r"Failed:\s*(\d+)",
            "skipped": r"Skipped:\s*(\d+)",
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, output)
            if match:
                value = int(match.group(1))
                if key == "total":
                    total = value
                elif key == "passed":
                    passed = value
                elif key == "failed":
                    failed = value
                elif key == "skipped":
                    skipped = value

        return TestRunResult(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=0,
        )

    def _parse_trx_results(self) -> list[TestCaseResult] | None:
        """Parse detailed results from TRX file."""
        if not self.results_path.exists():
            return None

        # Find most recent TRX file
        trx_files = list(self.results_path.glob("*.trx"))
        if not trx_files:
            return None

        trx_file = max(trx_files, key=lambda p: p.stat().st_mtime)

        try:
            tree = ET.parse(trx_file)
            root = tree.getroot()

            # Handle namespace
            ns = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}

            results = []

            for result in root.findall(".//t:UnitTestResult", ns):
                outcome = result.get("outcome", "NotExecuted")
                duration_str = result.get("duration", "0:0:0.0")

                # Parse duration
                try:
                    parts = duration_str.split(":")
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = float(parts[2])
                    duration_ms = (hours * 3600 + minutes * 60 + seconds) * 1000
                except Exception:
                    duration_ms = 0

                # Get test name
                test_name = result.get("testName", "Unknown")
                class_name = ""
                if "." in test_name:
                    parts = test_name.rsplit(".", 1)
                    class_name = parts[0]
                    test_name = parts[1]

                # Get error info if failed
                error_message = None
                stack_trace = None
                output_elem = result.find("t:Output", ns)
                if output_elem is not None:
                    error_info = output_elem.find("t:ErrorInfo", ns)
                    if error_info is not None:
                        msg_elem = error_info.find("t:Message", ns)
                        if msg_elem is not None:
                            error_message = msg_elem.text
                        stack_elem = error_info.find("t:StackTrace", ns)
                        if stack_elem is not None:
                            stack_trace = stack_elem.text

                results.append(TestCaseResult(
                    name=test_name,
                    class_name=class_name,
                    outcome=outcome,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    stack_trace=stack_trace,
                ))

            return results

        except Exception as e:
            logger.warning(f"Failed to parse TRX file: {e}")
            return None

    def get_failed_test_details(self, result: TestRunResult) -> list[dict]:
        """
        Get detailed information about failed tests.

        Args:
            result: Test run result

        Returns:
            List of failure details
        """
        failures = []
        for test in result.failed_tests:
            failures.append({
                "name": test.full_name,
                "error": test.error_message or "Unknown error",
                "stack_trace": test.stack_trace,
                "duration_ms": test.duration_ms,
            })
        return failures

    def get_summary(self, result: TestRunResult) -> dict:
        """
        Get test run summary.

        Args:
            result: Test run result

        Returns:
            Summary dictionary
        """
        return {
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "skipped": result.skipped,
            "duration_seconds": result.duration_seconds,
            "success": result.success,
            "pass_rate": result.passed / result.total if result.total > 0 else 0,
        }

    def cleanup_results(self) -> None:
        """Clean up test results directory."""
        import shutil
        if self.results_path.exists():
            shutil.rmtree(self.results_path, ignore_errors=True)
