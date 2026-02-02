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
    def is_source_file(self) -> bool:
        """Check if this is a source file under /src."""
        return self.path.startswith("src/") or "/src/" in self.path

    @property
    def is_test_file(self) -> bool:
        """Check if this is a test file under /tests."""
        return self.path.startswith("tests/") or "/tests/" in self.path

    @property
    def is_csharp_file(self) -> bool:
        """Check if this is a C# file."""
        return self.path.endswith(".cs")

    def get_corresponding_test_path(self) -> str | None:
        """
        Get the corresponding test file path for a source file.

        Assumes convention: src/Project/File.cs -> tests/Project.Tests/FileTests.cs
        """
        if not self.is_source_file or not self.is_csharp_file:
            return None

        # Convert src path to tests path
        # Example: src/MyProject/Services/UserService.cs
        #       -> tests/MyProject.Tests/Services/UserServiceTests.cs
        path_parts = self.path.replace("\\", "/").split("/")

        try:
            src_index = path_parts.index("src")
            if src_index + 1 >= len(path_parts):
                return None

            project_name = path_parts[src_index + 1]
            remaining_path = "/".join(path_parts[src_index + 2 :])

            # Add Tests suffix to filename
            if remaining_path.endswith(".cs"):
                remaining_path = remaining_path[:-3] + "Tests.cs"

            test_path = f"tests/{project_name}.Tests/{remaining_path}"
            return test_path

        except (ValueError, IndexError):
            return None


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

        changes = []
        for change in changes_data:
            item = change.get("item", {})
            path = item.get("path", "").lstrip("/")

            if not path:
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
    ) -> str:
        """
        Create a formatted summary comment for the PR.

        Args:
            tests_added: Number of new test files created
            tests_modified: Number of test files modified
            tests_deleted: Number of test files deleted
            test_results: Test execution results

        Returns:
            Formatted markdown comment
        """
        total_tests = test_results.get("total", 0)
        passed_tests = test_results.get("passed", 0)
        failed_tests = test_results.get("failed", 0)
        skipped_tests = test_results.get("skipped", 0)

        status_emoji = "✅" if failed_tests == 0 else "❌"

        comment = f"""## 🤖 AI Test Generation Summary

### Changes Made
| Action | Count |
|--------|-------|
| Tests Added | {tests_added} |
| Tests Modified | {tests_modified} |
| Tests Deleted | {tests_deleted} |

### Test Results {status_emoji}
| Metric | Value |
|--------|-------|
| Total Tests | {total_tests} |
| Passed | {passed_tests} |
| Failed | {failed_tests} |
| Skipped | {skipped_tests} |

---
*Generated by AI Test Generator using Qwen Coder 3*
"""
        return comment
