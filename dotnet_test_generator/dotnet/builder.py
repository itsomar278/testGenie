"""Build operations for .NET solutions."""

import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotnet_test_generator.core.exceptions import BuildError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BuildErrorInfo:
    """Information about a build error."""

    file: str
    line: int
    column: int
    code: str
    message: str
    severity: str = "error"


@dataclass
class BuildResult:
    """Result of a build operation."""

    success: bool
    duration_seconds: float
    errors: list[BuildErrorInfo] = field(default_factory=list)
    warnings: list[BuildErrorInfo] = field(default_factory=list)
    output: str = ""

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


class SolutionBuilder:
    """
    Handles .NET build operations.

    Provides methods for restoring, building, and cleaning solutions.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize builder.

        Args:
            repo_path: Path to repository root
        """
        self.repo_path = repo_path

    def restore(
        self,
        project: str | None = None,
        timeout: int = 300,
    ) -> bool:
        """
        Restore NuGet packages.

        Args:
            project: Optional project/solution path
            timeout: Timeout in seconds

        Returns:
            True if restore succeeded
        """
        logger.info("[DOTNET] Starting package restore")
        logger.info(f"[DOTNET] Working directory: {self.repo_path}")
        logger.info(f"[DOTNET] Project: {project or 'all'}")
        logger.info(f"[DOTNET] Timeout: {timeout}s")

        cmd = ["dotnet", "restore"]
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
            return result.returncode == 0

        except subprocess.TimeoutExpired:
            logger.error("Restore timed out")
            return False
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False

    def build(
        self,
        project: str | None = None,
        configuration: str = "Debug",
        no_restore: bool = True,
        timeout: int = 600,
    ) -> BuildResult:
        """
        Build the solution or project.

        Args:
            project: Optional project/solution path
            configuration: Build configuration
            no_restore: Skip restore step
            timeout: Timeout in seconds

        Returns:
            BuildResult with outcome details
        """
        logger.info("[DOTNET] Starting build")
        logger.info(f"[DOTNET] Configuration: {configuration}")
        logger.info(f"[DOTNET] No restore: {no_restore}")
        logger.info(f"[DOTNET] Project: {project or 'all'}")
        logger.info(f"[DOTNET] Timeout: {timeout}s")

        import time
        start_time = time.time()

        cmd = ["dotnet", "build", "-c", configuration]
        if no_restore:
            cmd.append("--no-restore")
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

            errors, warnings = self._parse_build_output(output)

            success = result.returncode == 0
            logger.info(f"[DOTNET] Build {'succeeded' if success else 'failed'} in {duration:.2f}s")
            logger.info(f"[DOTNET] Errors: {len(errors)}, Warnings: {len(warnings)}")
            if errors:
                for err in errors[:5]:
                    logger.error(f"[DOTNET]   {err.file}:{err.line} - {err.code}: {err.message}")
            if len(errors) > 5:
                logger.error(f"[DOTNET]   ... and {len(errors) - 5} more errors")

            return BuildResult(
                success=success,
                duration_seconds=duration,
                errors=errors,
                warnings=warnings,
                output=output,
            )

        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                duration_seconds=timeout,
                errors=[BuildErrorInfo(
                    file="",
                    line=0,
                    column=0,
                    code="TIMEOUT",
                    message=f"Build timed out after {timeout} seconds",
                )],
                output="Build timed out",
            )
        except Exception as e:
            return BuildResult(
                success=False,
                duration_seconds=0,
                errors=[BuildErrorInfo(
                    file="",
                    line=0,
                    column=0,
                    code="EXCEPTION",
                    message=str(e),
                )],
                output=str(e),
            )

    def _parse_build_output(
        self,
        output: str,
    ) -> tuple[list[BuildErrorInfo], list[BuildErrorInfo]]:
        """Parse MSBuild output for errors and warnings."""
        errors = []
        warnings = []

        # Pattern: file(line,col): error/warning CODE: message
        pattern = r"([^(]+)\((\d+),(\d+)\):\s*(error|warning)\s+(\w+):\s*(.+)"

        for line in output.split("\n"):
            match = re.search(pattern, line)
            if match:
                info = BuildErrorInfo(
                    file=match.group(1).strip(),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    severity=match.group(4),
                    code=match.group(5),
                    message=match.group(6).strip(),
                )

                if info.severity == "error":
                    errors.append(info)
                else:
                    warnings.append(info)

        return errors, warnings

    def clean(
        self,
        project: str | None = None,
        timeout: int = 120,
    ) -> bool:
        """
        Clean build outputs.

        Args:
            project: Optional project/solution path
            timeout: Timeout in seconds

        Returns:
            True if clean succeeded
        """
        logger.info("Cleaning build outputs")

        cmd = ["dotnet", "clean"]
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
            return result.returncode == 0

        except Exception as e:
            logger.error(f"Clean failed: {e}")
            return False

    def build_and_fix(
        self,
        fixer_callback,
        max_iterations: int = 10,
    ) -> BuildResult:
        """
        Build with automatic error fixing.

        Args:
            fixer_callback: Callback function(errors) that attempts to fix errors
            max_iterations: Maximum fix iterations

        Returns:
            Final BuildResult
        """
        for iteration in range(max_iterations):
            logger.info(f"Build attempt {iteration + 1}/{max_iterations}")

            result = self.build()

            if result.success:
                logger.info("Build succeeded")
                return result

            if not result.errors:
                logger.warning("Build failed but no errors parsed")
                return result

            logger.info(f"Build failed with {result.error_count} errors")

            # Attempt to fix errors
            errors_dict = [
                {
                    "file": e.file,
                    "line": e.line,
                    "column": e.column,
                    "code": e.code,
                    "message": e.message,
                }
                for e in result.errors
            ]

            fixed = fixer_callback(errors_dict)

            if not fixed:
                logger.warning("Fixer could not fix errors")
                return result

        logger.warning(f"Build failed after {max_iterations} iterations")
        return result
