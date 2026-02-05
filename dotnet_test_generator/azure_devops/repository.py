"""Repository management for Azure DevOps."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotnet_test_generator.azure_devops.client import AzureDevOpsClient
from dotnet_test_generator.core.exceptions import AzureDevOpsError, GitOperationError
from dotnet_test_generator.git.operations import GitOperations
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RepositoryInfo:
    """Repository information."""

    id: str
    name: str
    default_branch: str
    clone_url: str
    web_url: str
    project_name: str


class RepositoryManager:
    """
    Manages repository operations including cloning and branch handling.

    This class handles the deterministic (non-AI) operations for repository
    management in the Azure DevOps context.
    """

    def __init__(
        self,
        client: AzureDevOpsClient,
        work_directory: Path,
        personal_access_token: str,
    ):
        """
        Initialize repository manager.

        Args:
            client: Azure DevOps API client
            work_directory: Base directory for cloned repositories
            personal_access_token: PAT for Git authentication
        """
        self.client = client
        self.work_directory = work_directory
        self.pat = personal_access_token
        self.git_ops: GitOperations | None = None
        self._auth_config: list[str] | None = None

    def parse_repository_url(self, url: str) -> tuple[str, str, str]:
        """
        Parse Azure DevOps repository URL.

        Args:
            url: Repository URL

        Returns:
            Tuple of (organization, project, repository)

        Raises:
            AzureDevOpsError: If URL format is invalid
        """
        from urllib.parse import unquote
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")

        # Handle dev.azure.com format
        # https://dev.azure.com/org/project/_git/repo
        if "dev.azure.com" in parsed.netloc:
            if len(parts) >= 4 and parts[2] == "_git":
                return parsed.netloc.split("/")[-1] or parts[0], parts[1], unquote(parts[3])

        # Handle visualstudio.com format
        # https://org.visualstudio.com/project/_git/repo
        if "visualstudio.com" in parsed.netloc:
            org = parsed.netloc.split(".")[0]
            if len(parts) >= 3 and parts[1] == "_git":
                return org, parts[0], unquote(parts[2])

        # Handle internal/on-prem Azure DevOps Server format
        # https://server/collection/project/_git/repo
        # Example: https://devops.malaffi.ae/ADHDS/SFF-Sandbox-nonprod/_git/survey%20repo
        if "_git" in parsed.path:
            try:
                git_index = parts.index("_git")
                if git_index >= 2 and git_index + 1 < len(parts):
                    # collection/org is parts[0], project is parts[git_index-1], repo is parts[git_index+1]
                    org = parts[0]  # Collection name (e.g., ADHDS)
                    project = parts[git_index - 1]  # Project name
                    repo = unquote(parts[git_index + 1])  # Repo name (URL decoded)
                    logger.info(f"[URL] Parsed internal ADO URL: org={org}, project={project}, repo={repo}")
                    return org, project, repo
            except (ValueError, IndexError):
                pass

        raise AzureDevOpsError(
            f"Cannot parse repository URL: {url}. "
            "Expected format: https://dev.azure.com/org/project/_git/repo or "
            "https://server/collection/project/_git/repo"
        )

    def get_repository_info(self, repository_url: str) -> RepositoryInfo:
        """
        Get repository information from URL.

        Args:
            repository_url: Azure DevOps repository URL

        Returns:
            RepositoryInfo with repository details
        """
        org, project, repo_name = self.parse_repository_url(repository_url)
        logger.info(f"Fetching repository info: {org}/{project}/{repo_name}")

        try:
            repo_data = self.client.get_repository_by_name(repo_name)
        except AzureDevOpsError:
            # Try with repository ID if name lookup fails
            repos = self.client.list_repositories()
            repo_data = None
            for r in repos:
                if r["name"].lower() == repo_name.lower():
                    repo_data = r
                    break

            if not repo_data:
                raise AzureDevOpsError(f"Repository not found: {repo_name}")

        default_branch = repo_data.get("defaultBranch", "refs/heads/main")
        if default_branch.startswith("refs/heads/"):
            default_branch = default_branch[11:]

        return RepositoryInfo(
            id=repo_data["id"],
            name=repo_data["name"],
            default_branch=default_branch,
            clone_url=repo_data["remoteUrl"],
            web_url=repo_data["webUrl"],
            project_name=project,
        )

    def get_clone_path(self, repo_info: RepositoryInfo) -> Path:
        """Get local path for cloned repository."""
        return self.work_directory / repo_info.project_name / repo_info.name

    def _get_authenticated_clone_url(self, clone_url: str) -> str:
        """Create clone URL with embedded PAT authentication."""
        parsed = urlparse(clone_url)
        # Embed PAT in URL: https://PAT@dev.azure.com/...
        auth_url = f"{parsed.scheme}://{self.pat}@{parsed.netloc}{parsed.path}"
        return auth_url

    def _get_auth_header(self) -> str:
        """Get the authorization header for git extraheader."""
        import base64
        auth_string = base64.b64encode(f":{self.pat}".encode()).decode()
        return f"AUTHORIZATION: Basic {auth_string}"

    def clone_repository(
        self,
        repo_info: RepositoryInfo,
        branch: str | None = None,
        force_fresh: bool = True,
    ) -> Path:
        """
        Clone repository to local filesystem.

        Args:
            repo_info: Repository information
            branch: Branch to checkout (defaults to default branch)
            force_fresh: Delete existing clone and start fresh

        Returns:
            Path to cloned repository

        Raises:
            GitOperationError: On clone failure
        """
        clone_path = self.get_clone_path(repo_info)
        target_branch = branch or repo_info.default_branch

        logger.info(f"[CLONE] Repository: {repo_info.name}")
        logger.info(f"[CLONE] Target path: {clone_path}")
        logger.info(f"[CLONE] Target branch: {target_branch}")
        logger.info(f"[CLONE] Force fresh: {force_fresh}")

        # Store auth config for all git operations
        auth_header = self._get_auth_header()
        self._auth_config = [f'http.extraheader={auth_header}']

        if clone_path.exists():
            if force_fresh:
                logger.info("Removing existing repository clone")
                shutil.rmtree(clone_path, ignore_errors=True)
            else:
                logger.info("Repository already exists, using existing clone")
                self.git_ops = GitOperations(clone_path, extra_config=self._auth_config)
                # Fetch latest and checkout branch
                self.git_ops.fetch_all()
                self.git_ops.checkout(target_branch)
                return clone_path

        clone_path.parent.mkdir(parents=True, exist_ok=True)

        # Clone with authentication using extraheader (proxy-friendly)
        clone_url = repo_info.clone_url

        logger.info(f"Cloning repository: {repo_info.name}")
        logger.info(f"[CLONE] Using extraheader authentication (proxy-friendly)")
        try:
            self.git_ops = GitOperations.clone(
                url=clone_url,
                path=clone_path,
                branch=target_branch,
                extra_config=self._auth_config,
            )
        except GitOperationError as e:
            logger.error(f"Clone failed: {e}")
            raise

        logger.info(f"Repository cloned successfully to {clone_path}")
        return clone_path

    def checkout_pr_branch(self, repo_info: RepositoryInfo, pr_source_branch: str) -> None:
        """
        Checkout the PR source branch.

        Args:
            repo_info: Repository information
            pr_source_branch: PR source branch name
        """
        if not self.git_ops:
            clone_path = self.get_clone_path(repo_info)
            # Ensure auth config is set
            if not self._auth_config:
                auth_header = self._get_auth_header()
                self._auth_config = [f'http.extraheader={auth_header}']
            self.git_ops = GitOperations(clone_path, extra_config=self._auth_config)

        # Remove refs/heads/ prefix if present
        branch_name = pr_source_branch
        if branch_name.startswith("refs/heads/"):
            branch_name = branch_name[11:]

        logger.info(f"Checking out PR branch: {branch_name}")
        self.git_ops.fetch_all()
        self.git_ops.checkout(branch_name)

    def get_git_operations(self) -> GitOperations:
        """Get the GitOperations instance for the cloned repository."""
        if not self.git_ops:
            raise GitOperationError("Repository not cloned yet")
        return self.git_ops
