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


@dataclass
class _ProjectInfo:
    """Internal: info about a discovered .csproj project."""

    name: str       # e.g. "DashboardManagement.Application"
    dir_path: str   # relative dir from repo root, e.g. "Application" or "src/SurveyMgmt.Domain"
    proj_type: str  # 'source', 'test', 'skipped', 'other'
    layer: str | None  # 'application', 'domain', 'api', 'infrastructure', or None


class ChangeDetector:
    """
    Detects and analyzes changes in a pull request.

    Classifies files by scanning .csproj files to discover the actual project
    structure. Never relies on folder names — only .csproj naming conventions.
    """

    # Layers we generate unit tests for
    TESTABLE_LAYERS = {'domain', 'application'}
    # Layers we skip
    SKIPPED_LAYERS = {'api', 'infrastructure'}
    # All known DDD layers
    ALL_LAYERS = TESTABLE_LAYERS | SKIPPED_LAYERS

    def __init__(
        self,
        repo_path: Path,
        git_ops: GitOperations | None = None,
    ):
        self.repo_path = repo_path
        self.git_ops = git_ops or GitOperations(repo_path)
        self._projects: list[_ProjectInfo] | None = None

    # ------------------------------------------------------------------
    # Project discovery (the ONLY source of truth)
    # ------------------------------------------------------------------

    def _discover_projects(self) -> list[_ProjectInfo]:
        """
        Scan all .csproj files in the repo and classify each project.

        This is the single source of truth for determining which layer a
        file belongs to. Folder names are irrelevant — only .csproj naming
        matters.
        """
        if self._projects is not None:
            return self._projects

        self._projects = []

        try:
            for csproj in self.repo_path.rglob("*.csproj"):
                rel_str = str(csproj.relative_to(self.repo_path)).replace("\\", "/")
                rel_lower = rel_str.lower()

                # Skip build artifacts
                if any(skip in rel_lower for skip in ['bin/', 'obj/', 'packages/', 'node_modules/']):
                    continue

                proj_name = csproj.stem  # e.g. "DashboardManagement.Application"
                proj_lower = proj_name.lower()
                project_dir = csproj.parent
                rel_dir = str(project_dir.relative_to(self.repo_path)).replace("\\", "/")

                # Determine layer from .csproj name
                layer = self._extract_layer(proj_lower)

                # Determine project type
                if '.tests' in proj_lower or '.test' in proj_lower:
                    proj_type = 'test'
                elif layer in self.SKIPPED_LAYERS:
                    proj_type = 'skipped'
                elif layer in self.TESTABLE_LAYERS:
                    proj_type = 'source'
                else:
                    proj_type = 'other'

                info = _ProjectInfo(
                    name=proj_name,
                    dir_path=rel_dir,
                    proj_type=proj_type,
                    layer=layer,
                )
                self._projects.append(info)
                logger.info(f"[PROJECT] {proj_name} -> type={proj_type}, layer={layer}, dir={rel_dir}")

        except Exception as e:
            logger.error(f"[PROJECT] Error scanning .csproj files: {e}")

        return self._projects

    @staticmethod
    def _extract_layer(proj_name_lower: str) -> str | None:
        """
        Extract the DDD layer from a .csproj name (case-insensitive).

        Handles both source and test projects:
          "dashboardmanagement.application"       -> "application"
          "dashboardmanagement.application.tests" -> "application"
          "dashboardmanagement.domain.tests"      -> "domain"
          "dashboardmanagement.api"               -> "api"
        """
        # Strip .tests / .test suffix first
        name = proj_name_lower
        for suffix in ['.tests', '.test']:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
                break

        # Now check if it ends with a known layer
        for layer in ['application', 'domain', 'api', 'infrastructure']:
            if name.endswith(f'.{layer}'):
                return layer

        return None

    # ------------------------------------------------------------------
    # File classification
    # ------------------------------------------------------------------

    def _classify_file(self, file_path: str) -> tuple[str, _ProjectInfo | None]:
        """
        Classify a .cs file by finding which .csproj project directory it
        lives under.

        Returns (type, project_info) where type is one of:
          'source'  — testable layer (Domain, Application)
          'test'    — already a test file
          'skipped' — non-testable layer (Api, Infrastructure)
          'other'   — not under any known project
        """
        projects = self._discover_projects()
        norm = file_path.replace("\\", "/").lower()

        # Find the project whose directory is the deepest prefix of this file
        best: _ProjectInfo | None = None
        best_len = -1

        for proj in projects:
            proj_dir = proj.dir_path.lower()

            # Project at repo root (dir_path is ".")
            if proj_dir == ".":
                if best_len < 0:
                    best = proj
                    best_len = 0
                continue

            prefix = proj_dir + "/"
            if norm.startswith(prefix) and len(proj_dir) > best_len:
                best = proj
                best_len = len(proj_dir)

        if best:
            return best.proj_type, best

        return 'other', None

    # ------------------------------------------------------------------
    # Test path mapping
    # ------------------------------------------------------------------

    def _get_test_path_for_file(self, file_path: str) -> str | None:
        """
        Get the corresponding test file path for a source file.

        Uses .csproj discovery to:
        1. Find which source project the file belongs to
        2. Determine its layer (application, domain)
        3. Find the corresponding test project for that layer
        4. Mirror the internal folder structure
        """
        file_type, source_proj = self._classify_file(file_path)
        if file_type != 'source' or not source_proj or not source_proj.layer:
            return None

        # Find the test project for this layer
        test_proj = self._find_test_project_for_layer(source_proj.layer)
        if not test_proj:
            logger.warning(f"[PATH] No test project found for layer '{source_proj.layer}'")
            return None

        # Calculate remaining path within the source project
        norm_file = file_path.replace("\\", "/")
        source_dir = source_proj.dir_path

        # Strip the source project directory prefix to get the internal path
        # e.g. "Application/Services/DashboardService.cs" -> "Services/DashboardService.cs"
        if norm_file.lower().startswith(source_dir.lower() + "/"):
            remaining = norm_file[len(source_dir) + 1:]
        elif "/" in norm_file:
            remaining = norm_file.split("/", 1)[1]
        else:
            remaining = norm_file

        # Rename .cs -> Tests.cs
        if remaining.lower().endswith(".cs"):
            remaining = remaining[:-3] + "Tests.cs"

        return f"{test_proj.dir_path}/{remaining}"

    def _find_test_project_for_layer(self, layer: str) -> _ProjectInfo | None:
        """Find the test project that corresponds to a given DDD layer."""
        for proj in self._discover_projects():
            if proj.proj_type == 'test' and proj.layer == layer:
                return proj
        return None

    # ------------------------------------------------------------------
    # PR analysis (main entry point)
    # ------------------------------------------------------------------

    def analyze_pull_request(
        self,
        pr_info: PullRequestInfo,
        target_branch: str | None = None,
    ) -> ChangeAnalysis:
        """Analyze changes in a pull request using .csproj-based classification."""
        logger.info(f"[CHANGE] Analyzing {len(pr_info.changes)} changes in PR #{pr_info.id}")
        logger.info(f"[CHANGE] Target branch: {target_branch or pr_info.target_branch_name}")

        # Discover project structure upfront
        projects = self._discover_projects()
        logger.info(f"[CHANGE] Discovered {len(projects)} projects in repo:")
        for p in projects:
            logger.info(f"[CHANGE]   {p.name} ({p.proj_type}, layer={p.layer}) at {p.dir_path}")

        source_changes: list[ChangeContext] = []
        test_changes: list[FileChange] = []
        other_changes: list[FileChange] = []
        mappings: list[TestFileMapping] = []

        target = target_branch or pr_info.target_branch_name

        # Log all raw changes
        logger.info(f"[CHANGE] All PR changes ({len(pr_info.changes)} files):")
        for change in pr_info.changes:
            logger.info(f"[CHANGE]   - '{change.path}' (type={change.change_type.value})")

        for change in pr_info.changes:
            # Non-C# files -> skip
            if not change.is_csharp_file:
                logger.debug(f"[CHANGE]   -> Not a C# file: {change.path}")
                other_changes.append(change)
                continue

            # Non-testable files (GlobalUsings, migrations, designer, etc.)
            if change.is_non_testable_file:
                logger.info(f"[CHANGE]   -> Non-testable file: {change.path}")
                other_changes.append(change)
                continue

            # Classify using .csproj-based detection
            file_type, proj_info = self._classify_file(change.path)
            proj_label = proj_info.name if proj_info else "unknown"

            if file_type == 'test':
                logger.info(f"[CHANGE]   -> Test file ({proj_label}): {change.path}")
                test_changes.append(change)

            elif file_type == 'skipped':
                logger.info(f"[CHANGE]   -> Skipped layer ({proj_label}): {change.path}")
                other_changes.append(change)

            elif file_type == 'source':
                logger.info(f"[CHANGE]   -> SOURCE ({proj_label}): {change.path}")
                context = self._build_change_context(change, target)
                source_changes.append(context)

                # Create test file mapping
                test_path = self._get_test_path_for_file(change.path)
                if test_path:
                    test_exists = (self.repo_path / test_path).exists()
                    mappings.append(TestFileMapping(
                        source_path=change.path,
                        test_path=test_path,
                        source_exists=change.change_type != ChangeType.DELETE,
                        test_exists=test_exists,
                    ))
                    logger.info(f"[CHANGE]     -> Test path: {test_path} (exists: {test_exists})")
                else:
                    logger.warning(f"[CHANGE]     -> Could not determine test path")

            else:
                logger.info(f"[CHANGE]   -> No matching project: {change.path}")
                other_changes.append(change)

        logger.info(
            f"Analysis complete: {len(source_changes)} source, "
            f"{len(test_changes)} test, {len(other_changes)} other"
        )

        return ChangeAnalysis(
            source_changes=source_changes,
            test_changes=test_changes,
            other_changes=other_changes,
            mappings=mappings,
        )

    # ------------------------------------------------------------------
    # Change context building
    # ------------------------------------------------------------------

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

        # Get test file info using .csproj-based mapping
        test_path = self._get_test_path_for_file(change.path)
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

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _find_related_files(self, source_path: str) -> list[str]:
        """Find .cs files in the same directory as the source file."""
        related = []
        source_file = self.repo_path / source_path
        source_dir = source_file.parent

        if source_dir.exists():
            for sibling in source_dir.iterdir():
                if sibling.suffix == ".cs" and sibling != source_file:
                    related.append(str(sibling.relative_to(self.repo_path)))

        return related[:10]

    def get_file_diff(self, file_path: str, target_branch: str) -> str | None:
        """Get diff for a specific file."""
        try:
            return self.git_ops.diff(f"origin/{target_branch}", path=file_path)
        except Exception as e:
            logger.warning(f"Could not get diff for {file_path}: {e}")
            return None

    def suggest_test_project_path(self, source_path: str) -> str | None:
        """Suggest the test project path for a source file."""
        test_path = self._get_test_path_for_file(source_path)
        if test_path:
            return test_path.split("/")[0] if "/" in test_path else test_path
        return None

    def ensure_test_directory_exists(self, test_path: str) -> Path:
        """Ensure the directory for a test file exists."""
        test_file = self.repo_path / test_path
        test_dir = test_file.parent
        test_dir.mkdir(parents=True, exist_ok=True)
        return test_dir

    def get_nearby_context(
        self,
        source_path: str,
        max_files: int = 5,
    ) -> dict[str, str]:
        """Get content of nearby files for context."""
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
