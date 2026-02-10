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
    def is_source_file(self) -> bool:
        """Check if this is a source file (not a test file)."""
        norm = self.normalized_path

        # First check if it's a test file - if so, it's not a source file
        if self.is_test_file:
            return False

        # Check for src/ at start or /src/ anywhere
        if norm.startswith("src/") or "/src/" in norm:
            return True

        # Check for DDD project naming patterns at root level
        # e.g., ProjectName.Domain/, ProjectName.Application/, ProjectName.Infrastructure/
        ddd_patterns = ['.domain/', '.application/', '.infrastructure/', '.api/', '.web/', '.core/', '.shared/', '.common/']
        for pattern in ddd_patterns:
            if pattern in norm:
                return True

        # Also match if path starts with a project-like folder (contains a dot before the first slash)
        # e.g., "SurveyMgmt.Domain/Entities/..." or "MyApp.Core/Services/..."
        first_segment = norm.split('/')[0] if '/' in norm else norm
        if '.' in first_segment and not first_segment.endswith('.cs'):
            # It's a dotted folder name like "SurveyMgmt.Domain"
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

        Scans repo root for test projects (folders containing .csproj with 'test' in name),
        then matches based on layer name (domain, application, etc.)

        Args:
            source_project: Source project name (e.g., "SurveyMgmt.Domain")
            repo_path: Path to repo root

        Returns:
            Path to test project folder (e.g., "DomainTests") or None
        """
        source_lower = source_project.lower()

        # Common layer names to match
        layer_names = ['domain', 'application', 'infrastructure', 'api', 'web', 'core', 'shared', 'common']

        if not repo_path:
            return None

        # First, discover all test projects by scanning for .csproj files
        test_projects = self._discover_test_projects(repo_path)

        if not test_projects:
            logger.warning(f"[PATH] No test projects found in repo")
            return None

        logger.info(f"[PATH] Discovered test projects: {list(test_projects.keys())}")

        # Find which layer this source belongs to
        for layer in layer_names:
            if layer in source_lower:
                # Find test project containing this layer name
                for project_name, project_path in test_projects.items():
                    if layer in project_name.lower():
                        logger.info(f"[PATH] Matched source '{source_project}' (layer: {layer}) -> test project '{project_name}'")
                        return project_path
                break

        # No match found
        logger.warning(f"[PATH] No matching test project found for '{source_project}'")
        return None

    def _discover_test_projects(self, repo_path: Path) -> dict[str, str]:
        """
        Discover test projects by scanning for .csproj files containing 'test'.

        Args:
            repo_path: Path to repo root

        Returns:
            Dict mapping project name to relative path (e.g., {"DomainTests": "DomainTests"})
        """
        test_projects = {}

        try:
            # Scan repo root and one level deep for test .csproj files
            for item in repo_path.iterdir():
                if item.is_dir():
                    # Check if this folder contains a test .csproj
                    for csproj in item.glob("*.csproj"):
                        if "test" in csproj.stem.lower():
                            # Get relative path from repo root
                            rel_path = str(item.relative_to(repo_path)).replace("\\", "/")
                            test_projects[item.name] = rel_path
                            logger.debug(f"[PATH] Found test project: {item.name} at {rel_path}")
                            break

            # Also check tests/ folder if it exists
            tests_dir = repo_path / "tests"
            if tests_dir.exists() and tests_dir.is_dir():
                for item in tests_dir.iterdir():
                    if item.is_dir():
                        for csproj in item.glob("*.csproj"):
                            if "test" in csproj.stem.lower():
                                rel_path = str(item.relative_to(repo_path)).replace("\\", "/")
                                test_projects[item.name] = rel_path
                                logger.debug(f"[PATH] Found test project: {item.name} at {rel_path}")
                                break

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
