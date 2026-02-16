"""Pull request operations for Azure DevOps."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotnet_test_generator.azure_devops.client import AzureDevOpsClient
from dotnet_test_generator.azure_devops.repository import RepositoryInfo
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


class ChangeType(Enum):
    """Type of file change in a pull request."""

    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    RENAME = "rename"


@dataclass
class FileChange:
    """Represents a file change in a pull request."""

    path: str
    change_type: ChangeType
    original_path: str | None = None  # For renames

    @property
    def normalized_path(self) -> str:
        """Get normalized path with forward slashes and no leading slash."""
        return self.path.replace("\\", "/").lstrip("/").lower()

    @property
    def _project_segment(self) -> str:
        """Get the first meaningful project/layer segment (after stripping src/ if present)."""
        parts = self.normalized_path.split('/')
        if parts[0] == 'src' and len(parts) > 1:
            return parts[1]
        return parts[0]

    @property
    def is_skipped_layer(self) -> bool:
        """Check if this file belongs to a layer we skip for unit testing (Api, Infrastructure)."""
        norm = self.normalized_path
        # Dotted project names: SolutionName.Infrastructure/, SolutionName.Api/
        skip_patterns = ['.infrastructure/', '.api/']
        if any(pattern in norm for pattern in skip_patterns):
            return True
        # Bare folder names: Infrastructure/, Api/, src/Infrastructure/, src/Api/
        return self._project_segment in ('infrastructure', 'api')

    @property
    def is_non_testable_file(self) -> bool:
        """Check if this file type should be excluded from test generation."""
        norm = self.normalized_path
        name = norm.split('/')[-1] if '/' in norm else norm

        # Files that are never unit tested
        skip_names = ['globalusings.cs', 'global.cs', 'assemblyinfo.cs', 'program.cs', 'startup.cs']
        if name in skip_names:
            return True

        # Auto-generated / designer files
        skip_suffixes = ['.designer.cs', '.generated.cs', '.g.cs', '.g.i.cs']
        if any(name.endswith(s) for s in skip_suffixes):
            return True

        # EF Core migrations folder
        if '/migrations/' in norm:
            return True

        return False

    @property
    def is_source_file(self) -> bool:
        """Check if this is a testable source file."""
        norm = self.normalized_path

        # Exclusions first
        if self.is_test_file:
            return False
        if self.is_skipped_layer:
            return False
        if self.is_non_testable_file:
            return False

        # Check for src/ at start or /src/ anywhere
        if norm.startswith("src/") or "/src/" in norm:
            return True

        # Check for DDD project naming patterns
        # e.g., ProjectName.Domain/, ProjectName.Application/
        ddd_patterns = ['.domain/', '.application/', '.web/', '.core/', '.shared/', '.common/']
        for pattern in ddd_patterns:
            if pattern in norm:
                return True

        # Also match if path starts with a project-like folder (contains a dot before the first slash)
        # e.g., "SurveyMgmt.Domain/Entities/..." or "MyApp.Core/Services/..."
        first_segment = norm.split('/')[0] if '/' in norm else norm
        if '.' in first_segment and not first_segment.endswith('.cs'):
            return True

        # Bare DDD layer folder names: Application/, Domain/
        # (or src/Application/, src/Domain/)
        if self._project_segment in ('domain', 'application'):
            return True

        return False

    @property
    def is_test_file(self) -> bool:
        """Check if this is a test file under /tests or in a .Tests project."""
        norm = self.normalized_path
        # Check for tests/ folder
        if norm.startswith("tests/") or "/tests/" in norm:
            return True
        # Check for .Tests or .Test project naming
        if ".tests/" in norm or ".test/" in norm:
            return True
        return False

    @property
    def is_csharp_file(self) -> bool:
        """Check if this is a C# file."""
        return self.path.endswith(".cs")

    def get_corresponding_test_path(self, repo_path: Path | None = None) -> str | None:
        """
        Get the corresponding test file path for a source file.

        Discovers existing test projects by scanning for .csproj files,
        then maps source files to matching test projects.

        Args:
            repo_path: Optional path to repo root for detecting existing test projects
        """
        if not self.is_source_file or not self.is_csharp_file:
            return None

        path_parts = self.path.replace("\\", "/").split("/")

        # Handle src/ prefix: strip it to get the real project name
        # e.g., "src/MyApp.Domain/Entities/File.cs" → project_name = "MyApp.Domain"
        if path_parts[0].lower() == "src" and len(path_parts) >= 3:
            path_parts = path_parts[1:]

        if len(path_parts) >= 2:
            project_name = path_parts[0]  # e.g., "SurveyMgmt.Domain"
            remaining_path = "/".join(path_parts[1:])  # e.g., "Entities/File.cs"

            if remaining_path.endswith(".cs"):
                remaining_path = remaining_path[:-3] + "Tests.cs"

            # Find existing test project (returns full path from repo root)
            test_project_path = self._find_existing_test_project(project_name, repo_path)

            if test_project_path:
                return f"{test_project_path}/{remaining_path}"

        return None

    def _find_existing_test_project(self, source_project: str, repo_path: Path | None) -> str | None:
        """
        Find existing test project for a source project by scanning for .csproj files.

        Uses exact layer suffix matching (e.g., "MyApp.Domain" matches layer "domain")
        and finds the corresponding test project (e.g., "MyApp.Domain.Tests").

        Args:
            source_project: Source project name (e.g., "SurveyMgmt.Domain")
            repo_path: Path to repo root

        Returns:
            Path to test project folder (e.g., "SurveyMgmt.Domain.Tests") or None
        """
        source_lower = source_project.lower()

        # Only layers we write unit tests for
        testable_layers = ['domain', 'application']

        if not repo_path:
            return None

        # Discover all test projects by scanning for .csproj files recursively
        test_projects = self._discover_test_projects(repo_path)

        if not test_projects:
            logger.warning(f"[PATH] No test projects found in repo")
            return None

        logger.info(f"[PATH] Discovered test projects: {list(test_projects.keys())}")

        # Find which testable layer this source belongs to
        # Matches both dotted names ("MyApp.Domain") and bare folder names ("Domain")
        for layer in testable_layers:
            if source_lower.endswith(f".{layer}") or source_lower == layer:
                # Find test project matching this layer
                for project_name, project_path in test_projects.items():
                    project_lower = project_name.lower()
                    # Match: "MyApp.Domain.Tests" or "DomainTests"
                    if f".{layer}.tests" in project_lower or project_lower == f"{layer}tests":
                        logger.info(f"[PATH] Matched source '{source_project}' (layer: {layer}) -> test project '{project_name}'")
                        return project_path
                logger.warning(f"[PATH] Source '{source_project}' is {layer} layer but no test project found")
                break

        # No match found
        logger.warning(f"[PATH] No matching test project found for '{source_project}'")
        return None

    def _discover_test_projects(self, repo_path: Path) -> dict[str, str]:
        """
        Discover test projects by recursively scanning for .csproj files containing 'test'.

        Uses .rglob() to find test projects at any depth, supporting all folder layouts
        (root-level, src/tests, nested).

        Args:
            repo_path: Path to repo root

        Returns:
            Dict mapping project folder name to relative path
        """
        test_projects = {}

        try:
            for csproj in repo_path.rglob("*.csproj"):
                # Skip build artifacts
                rel_str = str(csproj.relative_to(repo_path)).replace("\\", "/").lower()
                if any(skip in rel_str for skip in ['bin/', 'obj/', 'packages/', 'node_modules/']):
                    continue

                if "test" in csproj.stem.lower():
                    project_dir = csproj.parent
                    rel_path = str(project_dir.relative_to(repo_path)).replace("\\", "/")
                    test_projects[project_dir.name] = rel_path
                    logger.debug(f"[PATH] Found test project: {project_dir.name} at {rel_path}")

        except Exception as e:
            logger.error(f"[PATH] Error discovering test projects: {e}")

        return test_projects


@dataclass
class PullRequestInfo:
    """Pull request information."""

    id: int
    title: str
    description: str
    source_branch: str
    target_branch: str
    status: str
    repository_id: str
    created_by: str
    changes: list[FileChange] = field(default_factory=list)

    @property
    def source_branch_name(self) -> str:
        """Get branch name without refs/heads/ prefix."""
        if self.source_branch.startswith("refs/heads/"):
            return self.source_branch[11:]
        return self.source_branch

    @property
    def target_branch_name(self) -> str:
        """Get branch name without refs/heads/ prefix."""
        if self.target_branch.startswith("refs/heads/"):
            return self.target_branch[11:]
        return self.target_branch

    def get_source_file_changes(self) -> list[FileChange]:
        """Get only source file changes (under /src, .cs files)."""
        return [
            c for c in self.changes
            if c.is_source_file and c.is_csharp_file
        ]


class PullRequestManager:
    """
    Manages pull request operations.

    Handles retrieving PR information, changes, and posting comments.
    """

    def __init__(self, client: AzureDevOpsClient):
        """
        Initialize pull request manager.

        Args:
            client: Azure DevOps API client
        """
        self.client = client

    def _map_change_type(self, api_change_type: str) -> ChangeType:
        """Map Azure DevOps change type to our enum."""
        mapping = {
            "add": ChangeType.ADD,
            "edit": ChangeType.EDIT,
            "delete": ChangeType.DELETE,
            "rename": ChangeType.RENAME,
            "1": ChangeType.ADD,  # Numeric values from API
            "2": ChangeType.EDIT,
            "16": ChangeType.DELETE,
            "8": ChangeType.RENAME,
        }
        return mapping.get(str(api_change_type).lower(), ChangeType.EDIT)

    def get_pull_request(
        self,
        repo_info: RepositoryInfo,
        pull_request_id: int,
    ) -> PullRequestInfo:
        """
        Get pull request information including changes.

        Args:
            repo_info: Repository information
            pull_request_id: Pull request ID

        Returns:
            PullRequestInfo with full details
        """
        logger.info(f"Fetching PR #{pull_request_id}")

        # Get PR details
        pr_data = self.client.get_pull_request(repo_info.id, pull_request_id)

        # Get changes
        changes_data = self.client.get_pull_request_changes(repo_info.id, pull_request_id)

        logger.info(f"[PR] Raw changes data from API: {len(changes_data)} items")

        changes = []
        for change in changes_data:
            item = change.get("item", {})
            raw_path = item.get("path", "")
            path = raw_path.lstrip("/")

            logger.info(f"[PR] API change: raw_path='{raw_path}' -> path='{path}', changeType={change.get('changeType')}")

            if not path:
                logger.warning(f"[PR] Empty path, skipping: {change}")
                continue

            change_type = self._map_change_type(change.get("changeType", "edit"))

            original_path = None
            if change_type == ChangeType.RENAME:
                source_item = change.get("sourceServerItem")
                if source_item:
                    original_path = source_item.lstrip("/")

            changes.append(FileChange(
                path=path,
                change_type=change_type,
                original_path=original_path,
            ))

        return PullRequestInfo(
            id=pull_request_id,
            title=pr_data.get("title", ""),
            description=pr_data.get("description", ""),
            source_branch=pr_data.get("sourceRefName", ""),
            target_branch=pr_data.get("targetRefName", ""),
            status=pr_data.get("status", ""),
            repository_id=repo_info.id,
            created_by=pr_data.get("createdBy", {}).get("displayName", ""),
            changes=changes,
        )

    def get_file_content_at_branch(
        self,
        repo_info: RepositoryInfo,
        file_path: str,
        branch: str,
    ) -> str | None:
        """
        Get file content at a specific branch.

        Args:
            repo_info: Repository information
            file_path: Path to file in repository
            branch: Branch name

        Returns:
            File content or None if file doesn't exist
        """
        try:
            return self.client.get_file_content(
                repository_id=repo_info.id,
                path=file_path,
                version=branch,
                version_type="branch",
            )
        except Exception as e:
            logger.debug(f"Could not get file {file_path} at {branch}: {e}")
            return None

    def post_comment(
        self,
        repo_info: RepositoryInfo,
        pull_request_id: int,
        comment: str,
    ) -> None:
        """
        Post a comment to a pull request.

        Args:
            repo_info: Repository information
            pull_request_id: Pull request ID
            comment: Comment content (markdown)
        """
        logger.info(f"Posting comment to PR #{pull_request_id}")
        self.client.create_pull_request_comment(
            repository_id=repo_info.id,
            pull_request_id=pull_request_id,
            content=comment,
        )

    def create_test_summary_comment(
        self,
        tests_added: int,
        tests_modified: int,
        tests_deleted: int,
        test_results: dict,
        test_files_created: int = 0,
        test_files_modified: int = 0,
        total_test_methods: int = 0,
    ) -> str:
        """
        Create a formatted summary comment for the PR.

        Args:
            tests_added: Number of test methods added (deprecated, use total_test_methods)
            tests_modified: Number of test files modified
            tests_deleted: Number of test files deleted
            test_results: Test execution results
            test_files_created: Number of new test files created
            test_files_modified: Number of existing test files modified
            total_test_methods: Total number of test methods ([Fact]/[Theory]) written

        Returns:
            Formatted markdown comment
        """
        total_tests = test_results.get("total", 0)
        passed_tests = test_results.get("passed", 0)
        failed_tests = test_results.get("failed", 0)
        skipped_tests = test_results.get("skipped", 0)

        # Use total_test_methods if provided, otherwise fall back to tests_added
        test_method_count = total_test_methods if total_test_methods > 0 else tests_added

        status_emoji = "✅" if failed_tests == 0 and total_tests > 0 else ("⚠️" if total_tests == 0 else "❌")

        comment = f"""## 🤖 AI Test Generation Summary

### Changes Made
| Action | Count |
|--------|-------|
| Test Files Created | {test_files_created or tests_added} |
| Test Files Modified | {test_files_modified or tests_modified} |
| Test Files Deleted | {tests_deleted} |
| **Test Methods Written** | **{test_method_count}** |

### Test Execution Results {status_emoji}
| Metric | Value |
|--------|-------|
| Total Tests Discovered | {total_tests} |
| Passed | {passed_tests} |
| Failed | {failed_tests} |
| Skipped | {skipped_tests} |

---
*Generated by AI Test Generator using Qwen Coder 3*
"""
        return comment
