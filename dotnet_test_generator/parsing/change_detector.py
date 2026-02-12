"""Change detection for pull requests."""

from dataclasses import dataclass, field
from pathlib import Path

from dotnet_test_generator.azure_devops.pull_request import (
    FileChange,
    ChangeType,
    PullRequestInfo,
)
from dotnet_test_generator.git.operations import GitOperations
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TestFileMapping:
    """Mapping between source file and test file."""

    source_path: str
    test_path: str
    source_exists: bool = True
    test_exists: bool = False


@dataclass
class ChangeContext:
    """Context for a file change including related files."""

    change: FileChange
    source_content_old: str | None = None
    source_content_new: str | None = None
    test_content_current: str | None = None
    test_file_path: str | None = None
    related_files: list[str] = field(default_factory=list)


@dataclass
class ChangeAnalysis:
    """Analysis of all changes in a pull request."""

    source_changes: list[ChangeContext]
    test_changes: list[FileChange]
    other_changes: list[FileChange]
    mappings: list[TestFileMapping]

    @property
    def files_needing_tests(self) -> list[ChangeContext]:
        """Get source changes that need test generation."""
        return [
            c for c in self.source_changes
            if c.change.change_type != ChangeType.DELETE
        ]

    @property
    def files_with_deleted_tests(self) -> list[ChangeContext]:
        """Get source changes where source was deleted."""
        return [
            c for c in self.source_changes
            if c.change.change_type == ChangeType.DELETE
        ]


class ChangeDetector:
    """
    Detects and analyzes changes in a pull request.

    Identifies source file changes and maps them to corresponding test files.
    """

    def __init__(
        self,
        repo_path: Path,
        git_ops: GitOperations | None = None,
    ):
        """
        Initialize change detector.

        Args:
            repo_path: Path to repository root
            git_ops: Git operations instance (created if not provided)
        """
        self.repo_path = repo_path
        self.git_ops = git_ops or GitOperations(repo_path)

    def analyze_pull_request(
        self,
        pr_info: PullRequestInfo,
        target_branch: str | None = None,
    ) -> ChangeAnalysis:
        """
        Analyze changes in a pull request.

        Args:
            pr_info: Pull request information with changes
            target_branch: Target branch for comparison

        Returns:
            ChangeAnalysis with categorized changes
        """
        logger.info(f"[CHANGE] Analyzing {len(pr_info.changes)} changes in PR #{pr_info.id}")
        logger.info(f"[CHANGE] Target branch: {target_branch or pr_info.target_branch_name}")

        source_changes = []
        test_changes = []
        other_changes = []
        mappings = []

        target = target_branch or pr_info.target_branch_name

        # Log all changes for debugging
        logger.info(f"[CHANGE] All PR changes ({len(pr_info.changes)} files):")
        for change in pr_info.changes:
            logger.info(f"[CHANGE]   - '{change.path}' (type={change.change_type.value}, cs={change.is_csharp_file}, src={change.is_source_file}, test={change.is_test_file})")

        for change in pr_info.changes:
            logger.debug(f"[CHANGE] Processing: {change.path} ({change.change_type.value})")
            if not change.is_csharp_file:
                logger.debug(f"[CHANGE]   -> Not a C# file, skipping")
                other_changes.append(change)
                continue

            if change.is_test_file:
                logger.debug(f"[CHANGE]   -> Test file")
                test_changes.append(change)
                continue

            # Skip layers we don't unit test (Api, Infrastructure)
            if change.is_skipped_layer:
                logger.info(f"[CHANGE]   -> Skipped layer (Api/Infrastructure): {change.path}")
                other_changes.append(change)
                continue

            # Skip non-testable files (migrations, designer, global usings, etc.)
            if change.is_non_testable_file:
                logger.info(f"[CHANGE]   -> Non-testable file: {change.path}")
                other_changes.append(change)
                continue

            if change.is_source_file:
                logger.info(f"[CHANGE]   -> SOURCE FILE DETECTED: {change.path}")
                context = self._build_change_context(change, target)
                source_changes.append(context)

                # Create mapping
                test_path = change.get_corresponding_test_path(self.repo_path)
                if test_path:
                    test_exists = (self.repo_path / test_path).exists()
                    mappings.append(TestFileMapping(
                        source_path=change.path,
                        test_path=test_path,
                        source_exists=change.change_type != ChangeType.DELETE,
                        test_exists=test_exists,
                    ))
                    logger.info(f"[CHANGE]     -> Test path mapping: {test_path} (exists: {test_exists})")
            else:
                logger.info(f"[CHANGE]   -> C# file but NOT source or test: {change.path}")
                other_changes.append(change)

        logger.info(
            f"Analysis complete: {len(source_changes)} source changes, "
            f"{len(test_changes)} test changes, {len(other_changes)} other"
        )

        return ChangeAnalysis(
            source_changes=source_changes,
            test_changes=test_changes,
            other_changes=other_changes,
            mappings=mappings,
        )

    def _build_change_context(
        self,
        change: FileChange,
        target_branch: str,
    ) -> ChangeContext:
        """Build context for a file change."""
        context = ChangeContext(change=change)

        file_path = self.repo_path / change.path

        # Get old content (from target branch)
        if change.change_type in (ChangeType.EDIT, ChangeType.DELETE):
            context.source_content_old = self.git_ops.get_file_content_at_ref(
                change.path,
                f"origin/{target_branch}",
            )

        # Get new content (current working tree)
        if change.change_type != ChangeType.DELETE and file_path.exists():
            try:
                context.source_content_new = file_path.read_text(encoding="utf-8-sig")
            except Exception as e:
                logger.warning(f"Could not read {file_path}: {e}")

        # Get test file info
        test_path = change.get_corresponding_test_path(self.repo_path)
        if test_path:
            context.test_file_path = test_path
            test_file = self.repo_path / test_path
            if test_file.exists():
                try:
                    context.test_content_current = test_file.read_text(encoding="utf-8-sig")
                except Exception as e:
                    logger.warning(f"Could not read test file {test_file}: {e}")

        # Find related files
        context.related_files = self._find_related_files(change.path)

        return context

    def _find_related_files(self, source_path: str) -> list[str]:
        """
        Find files related to a source file.

        Looks for:
        - Files in same directory
        - Interface definitions
        - Base classes
        """
        related = []
        source_file = self.repo_path / source_path
        source_dir = source_file.parent

        if source_dir.exists():
            # Files in same directory
            for sibling in source_dir.iterdir():
                if sibling.suffix == ".cs" and sibling != source_file:
                    related.append(str(sibling.relative_to(self.repo_path)))

        # Limit to prevent context overflow
        return related[:10]

    def get_file_diff(self, file_path: str, target_branch: str) -> str | None:
        """
        Get diff for a specific file.

        Args:
            file_path: Path to file
            target_branch: Branch to compare against

        Returns:
            Diff output or None
        """
        try:
            return self.git_ops.diff(f"origin/{target_branch}", path=file_path)
        except Exception as e:
            logger.warning(f"Could not get diff for {file_path}: {e}")
            return None

    def suggest_test_project_path(self, source_path: str) -> str | None:
        """
        Suggest the test project path for a source file by scanning for .csproj files.

        Args:
            source_path: Source file path

        Returns:
            Suggested test project directory or None
        """
        change = FileChange(path=source_path, change_type=ChangeType.EDIT)
        test_path = change.get_corresponding_test_path(self.repo_path)
        if test_path:
            # Return just the project directory part (first segment of test path)
            return test_path.split("/")[0] if "/" in test_path else test_path
        return None

    def ensure_test_directory_exists(self, test_path: str) -> Path:
        """
        Ensure the directory for a test file exists.

        Args:
            test_path: Test file path

        Returns:
            Path to the test directory
        """
        test_file = self.repo_path / test_path
        test_dir = test_file.parent
        test_dir.mkdir(parents=True, exist_ok=True)
        return test_dir

    def get_nearby_context(
        self,
        source_path: str,
        max_files: int = 5,
    ) -> dict[str, str]:
        """
        Get content of nearby files for context.

        Args:
            source_path: Source file path
            max_files: Maximum number of files to include

        Returns:
            Dictionary mapping file paths to contents
        """
        context = {}
        source_file = self.repo_path / source_path
        source_dir = source_file.parent

        if not source_dir.exists():
            return context

        count = 0
        for sibling in sorted(source_dir.iterdir()):
            if count >= max_files:
                break

            if sibling.suffix == ".cs" and sibling != source_file:
                try:
                    content = sibling.read_text(encoding="utf-8-sig")
                    rel_path = str(sibling.relative_to(self.repo_path))
                    context[rel_path] = content
                    count += 1
                except Exception:
                    pass

        return context
