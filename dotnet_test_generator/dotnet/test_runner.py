"""Test execution for .NET projects."""

import subprocess
import re
import time
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
    Full transparency logging for debugging.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize test runner.

        Args:
            repo_path: Path to repository root
        """
        self.repo_path = repo_path
        self.results_path = repo_path / "TestResults"

    def _discover_test_projects(self) -> list[Path]:
        """
        Discover test projects by looking for .csproj files with xUnit references.

        Returns:
            List of test project paths
        """
        logger.info("[TEST] Discovering test projects...")

        test_projects = []

        # Find all .csproj files that might be test projects
        for csproj in self.repo_path.glob("**/*.csproj"):
            # Skip if in bin/obj directories
            if "bin" in csproj.parts or "obj" in csproj.parts:
                continue

            # Check if it looks like a test project
            csproj_name = csproj.stem.lower()
            if "test" in csproj_name:
                # Verify it has xUnit reference
                try:
                    content = csproj.read_text()
                    if "xunit" in content.lower():
                        test_projects.append(csproj)
                        logger.info(f"[TEST]   ✓ {csproj.relative_to(self.repo_path)} (has xUnit)")
                    else:
                        logger.info(f"[TEST]   - {csproj.relative_to(self.repo_path)} (no xUnit reference)")
                except Exception as e:
                    logger.warning(f"[TEST]   ? {csproj.relative_to(self.repo_path)} (could not read: {e})")

        logger.info(f"[TEST] Found {len(test_projects)} test projects with xUnit")
        return test_projects

    def run_tests(
        self,
        project: str | None = None,
        filter_expr: str | None = None,
        no_build: bool = False,
        timeout: int = 600,
        collect_coverage: bool = False,
    ) -> TestRunResult:
        """
        Run tests with full transparency logging.

        Args:
            project: Optional test project path
            filter_expr: Test filter expression
            no_build: Skip build step
            timeout: Timeout in seconds
            collect_coverage: Collect code coverage

        Returns:
            TestRunResult with execution details
        """
        logger.info("=" * 60)
        logger.info("[TEST] STEP: TEST EXECUTION")
        logger.info("=" * 60)

        start_time = time.time()

        # Discover test projects
        test_projects = self._discover_test_projects()

        if not test_projects:
            logger.warning("[TEST] No test projects found!")
            logger.warning("[TEST] Searched for .csproj files with 'test' in name and xUnit reference")
            return TestRunResult(
                total=0,
                passed=0,
                failed=0,
                skipped=0,
                duration_seconds=0,
                output="No test projects found",
                success=True,  # Not a failure, just nothing to run
            )

        # Build command with detailed output
        # Use console logger with detailed verbosity to capture all output
        cmd = [
            "dotnet", "test",
            "--logger", "trx",
            "--logger", "console;verbosity=detailed",
            "--results-directory", str(self.results_path),
            "--verbosity", "detailed",  # Full MSBuild/test output
        ]

        if no_build:
            cmd.append("--no-build")
            logger.info("[TEST] Skipping build (--no-build)")
        else:
            logger.info("[TEST] Will build test projects before running")

        if filter_expr:
            cmd.extend(["--filter", filter_expr])
            logger.info(f"[TEST] Filter: {filter_expr}")

        if collect_coverage:
            cmd.extend(["--collect", "XPlat Code Coverage"])
            logger.info("[TEST] Collecting code coverage")

        if project:
            cmd.append(project)
            logger.info(f"[TEST] Project: {project}")

        logger.info(f"[TEST] Command: {' '.join(cmd)}")
        logger.info(f"[TEST] Working directory: {self.repo_path}")
        logger.info(f"[TEST] Timeout: {timeout}s")
        logger.info("-" * 40)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration = time.time() - start_time

            # Capture output
            stdout_output = result.stdout or ""
            stderr_output = result.stderr or ""
            combined_output = stdout_output + stderr_output

            logger.info(f"[TEST] Completed in {duration:.1f}s with exit code {result.returncode}")

            # Log full output for transparency
            logger.info("[TEST] === BEGIN TEST OUTPUT ===")
            for line in combined_output.split('\n'):
                if line.strip():
                    line_lower = line.lower()
                    if 'error' in line_lower or 'failed' in line_lower:
                        logger.error(f"[TEST] {line}")
                    elif 'warning' in line_lower:
                        logger.warning(f"[TEST] {line}")
                    elif 'passed' in line_lower:
                        logger.info(f"[TEST] {line}")
                    else:
                        logger.info(f"[TEST] {line}")
            logger.info("[TEST] === END TEST OUTPUT ===")

            # Parse results from console output
            test_result = self._parse_console_output(combined_output)
            test_result.duration_seconds = duration
            test_result.output = combined_output
            test_result.success = result.returncode == 0

            # Try to get detailed results from TRX file
            trx_results = self._parse_trx_results()
            if trx_results:
                test_result.test_cases = trx_results
                logger.info(f"[TEST] Found {len(trx_results)} test cases in TRX file")

                # If console parsing failed but TRX has data, use TRX counts
                if test_result.total == 0 and len(trx_results) > 0:
                    test_result.total = len(trx_results)
                    test_result.passed = len([t for t in trx_results if t.outcome == "Passed"])
                    test_result.failed = len([t for t in trx_results if t.outcome == "Failed"])
                    test_result.skipped = len([t for t in trx_results if t.outcome in ("Skipped", "NotExecuted")])

            # Summary
            logger.info("-" * 40)
            if test_result.success:
                logger.info(f"[TEST] ✓ Tests PASSED")
            else:
                logger.error(f"[TEST] ✗ Tests FAILED")

            logger.info(f"[TEST] Total: {test_result.total}")
            logger.info(f"[TEST] Passed: {test_result.passed}")
            logger.info(f"[TEST] Failed: {test_result.failed}")
            logger.info(f"[TEST] Skipped: {test_result.skipped}")

            if test_result.failed > 0 and test_result.failed_tests:
                logger.error("[TEST] Failed tests:")
                for test in test_result.failed_tests[:10]:
                    logger.error(f"[TEST]   - {test.full_name}")
                    if test.error_message:
                        logger.error(f"[TEST]     Error: {test.error_message[:200]}")

            if test_result.total == 0:
                logger.warning("[TEST] No tests were discovered!")
                logger.warning("[TEST] Possible causes:")
                logger.warning("[TEST]   - Test files don't have [Fact] or [Theory] attributes")
                logger.warning("[TEST]   - Test project missing xunit.runner.visualstudio package")
                logger.warning("[TEST]   - Build failed (check Step 7 output)")
                logger.warning("[TEST]   - Using statements are incorrect")

            return test_result

        except subprocess.TimeoutExpired:
            logger.error(f"[TEST] ✗ Test run timed out after {timeout}s")
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
            logger.error(f"[TEST] ✗ Test run failed: {e}")
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

        # Multiple patterns for different dotnet test output formats
        patterns_list = [
            # Standard format
            {
                "total": r"Total:\s+(\d+)",
                "passed": r"Passed:\s+(\d+)",
                "failed": r"Failed:\s+(\d+)",
                "skipped": r"Skipped:\s+(\d+)",
            },
            # "Total tests: X" format
            {
                "total": r"Total tests:\s*(\d+)",
                "passed": r"Passed:\s*(\d+)",
                "failed": r"Failed:\s*(\d+)",
                "skipped": r"Skipped:\s*(\d+)",
            },
        ]

        for patterns in patterns_list:
            temp = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

            for key, pattern in patterns.items():
                match = re.search(pattern, output, re.IGNORECASE)
                if match:
                    temp[key] = int(match.group(1))

            if temp["total"] > 0 or temp["passed"] > 0:
                total, passed, failed, skipped = temp["total"], temp["passed"], temp["failed"], temp["skipped"]
                logger.info(f"[TEST] Parsed from console: Total={total}, Passed={passed}, Failed={failed}, Skipped={skipped}")
                break

        # Fallback: count test result lines
        if total == 0:
            passed_count = len(re.findall(r"^\s*Passed\s+\S+", output, re.MULTILINE))
            failed_count = len(re.findall(r"^\s*Failed\s+\S+", output, re.MULTILINE))
            if passed_count > 0 or failed_count > 0:
                passed, failed = passed_count, failed_count
                total = passed + failed
                logger.info(f"[TEST] Counted from lines: Total={total}, Passed={passed}, Failed={failed}")

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
            logger.info("[TEST] No TestResults directory found")
            return None

        trx_files = list(self.results_path.glob("*.trx"))
        if not trx_files:
            logger.info("[TEST] No TRX files found")
            return None

        trx_file = max(trx_files, key=lambda p: p.stat().st_mtime)
        logger.info(f"[TEST] Parsing TRX file: {trx_file.name}")

        try:
            tree = ET.parse(trx_file)
            root = tree.getroot()

            ns = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
            results = []

            for result in root.findall(".//t:UnitTestResult", ns):
                outcome = result.get("outcome", "NotExecuted")
                duration_str = result.get("duration", "0:0:0.0")

                try:
                    parts = duration_str.split(":")
                    hours, minutes = int(parts[0]), int(parts[1])
                    seconds = float(parts[2])
                    duration_ms = (hours * 3600 + minutes * 60 + seconds) * 1000
                except Exception:
                    duration_ms = 0

                test_name = result.get("testName", "Unknown")
                class_name = ""
                if "." in test_name:
                    parts = test_name.rsplit(".", 1)
                    class_name, test_name = parts[0], parts[1]

                error_message = stack_trace = None
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
            logger.warning(f"[TEST] Failed to parse TRX file: {e}")
            return None

    def get_failed_test_details(self, result: TestRunResult) -> list[dict]:
        """Get detailed information about failed tests."""
        return [
            {
                "name": test.full_name,
                "error": test.error_message or "Unknown error",
                "stack_trace": test.stack_trace,
                "duration_ms": test.duration_ms,
            }
            for test in result.failed_tests
        ]

    def get_summary(self, result: TestRunResult) -> dict:
        """Get test run summary."""
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
