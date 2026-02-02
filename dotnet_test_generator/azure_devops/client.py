"""Azure DevOps REST API client."""

import base64
from typing import Any
from urllib.parse import urljoin

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from dotnet_test_generator.core.exceptions import AzureDevOpsError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


class AzureDevOpsClient:
    """
    Client for Azure DevOps REST API operations.

    Handles authentication, request retries, and error handling.
    """

    API_VERSION = "7.1"

    def __init__(
        self,
        organization_url: str,
        personal_access_token: str,
        project: str,
        timeout: int = 30,
    ):
        """
        Initialize Azure DevOps client.

        Args:
            organization_url: Organization URL (e.g., https://dev.azure.com/org)
            personal_access_token: PAT for authentication
            project: Project name
            timeout: Request timeout in seconds
        """
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.timeout = timeout

        # Create auth header
        auth_string = base64.b64encode(f":{personal_access_token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {auth_string}",
            "Content-Type": "application/json",
        }

        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "AzureDevOpsClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _build_url(self, path: str, is_git_api: bool = False) -> str:
        """Build full API URL."""
        if is_git_api:
            base = f"{self.organization_url}/{self.project}/_apis/git"
        else:
            base = f"{self.organization_url}/{self.project}/_apis"

        url = urljoin(base + "/", path.lstrip("/"))

        # Add API version
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}api-version={self.API_VERSION}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        is_git_api: bool = False,
        **kwargs: Any,
    ) -> dict:
        """
        Make authenticated request to Azure DevOps API.

        Args:
            method: HTTP method
            path: API path
            is_git_api: Whether this is a Git API endpoint
            **kwargs: Additional request arguments

        Returns:
            Response JSON

        Raises:
            AzureDevOpsError: On API errors
        """
        url = self._build_url(path, is_git_api)
        logger.debug(f"API Request: {method} {url}")

        try:
            response = self.client.request(method, url, **kwargs)

            if response.status_code >= 400:
                raise AzureDevOpsError(
                    f"API request failed: {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if response.status_code == 204:
                return {}

            return response.json()

        except httpx.RequestError as e:
            raise AzureDevOpsError(f"Request failed: {e}") from e

    def get(self, path: str, is_git_api: bool = False, **kwargs: Any) -> dict:
        """Make GET request."""
        return self._request("GET", path, is_git_api, **kwargs)

    def post(self, path: str, is_git_api: bool = False, **kwargs: Any) -> dict:
        """Make POST request."""
        return self._request("POST", path, is_git_api, **kwargs)

    def patch(self, path: str, is_git_api: bool = False, **kwargs: Any) -> dict:
        """Make PATCH request."""
        return self._request("PATCH", path, is_git_api, **kwargs)

    def delete(self, path: str, is_git_api: bool = False, **kwargs: Any) -> dict:
        """Make DELETE request."""
        return self._request("DELETE", path, is_git_api, **kwargs)

    # Convenience methods for common operations

    def get_repository(self, repository_id: str) -> dict:
        """Get repository details."""
        return self.get(f"repositories/{repository_id}", is_git_api=True)

    def get_repository_by_name(self, name: str) -> dict:
        """Get repository by name."""
        repos = self.get("repositories", is_git_api=True)
        for repo in repos.get("value", []):
            if repo["name"].lower() == name.lower():
                return repo
        raise AzureDevOpsError(f"Repository not found: {name}")

    def list_repositories(self) -> list[dict]:
        """List all repositories in the project."""
        response = self.get("repositories", is_git_api=True)
        return response.get("value", [])

    def get_pull_request(self, repository_id: str, pull_request_id: int) -> dict:
        """Get pull request details."""
        return self.get(
            f"repositories/{repository_id}/pullrequests/{pull_request_id}",
            is_git_api=True,
        )

    def get_pull_request_iterations(
        self,
        repository_id: str,
        pull_request_id: int,
    ) -> list[dict]:
        """Get pull request iterations."""
        response = self.get(
            f"repositories/{repository_id}/pullrequests/{pull_request_id}/iterations",
            is_git_api=True,
        )
        return response.get("value", [])

    def get_pull_request_changes(
        self,
        repository_id: str,
        pull_request_id: int,
        iteration_id: int | None = None,
    ) -> list[dict]:
        """
        Get files changed in a pull request.

        Args:
            repository_id: Repository ID
            pull_request_id: Pull request ID
            iteration_id: Optional iteration ID (defaults to latest)

        Returns:
            List of changed files
        """
        if iteration_id is None:
            iterations = self.get_pull_request_iterations(repository_id, pull_request_id)
            if iterations:
                iteration_id = iterations[-1]["id"]
            else:
                iteration_id = 1

        response = self.get(
            f"repositories/{repository_id}/pullrequests/{pull_request_id}"
            f"/iterations/{iteration_id}/changes",
            is_git_api=True,
        )
        return response.get("changeEntries", [])

    def get_file_content(
        self,
        repository_id: str,
        path: str,
        version: str | None = None,
        version_type: str = "branch",
    ) -> str:
        """
        Get file content from repository.

        Args:
            repository_id: Repository ID
            path: File path in repository
            version: Version (branch name, commit SHA, or tag)
            version_type: Type of version (branch, commit, tag)

        Returns:
            File content as string
        """
        params = {"path": path}
        if version:
            params["versionDescriptor.version"] = version
            params["versionDescriptor.versionType"] = version_type

        url = self._build_url(f"repositories/{repository_id}/items", is_git_api=True)
        response = self.client.get(url, params=params)

        if response.status_code >= 400:
            raise AzureDevOpsError(
                f"Failed to get file: {path}",
                status_code=response.status_code,
                response_body=response.text,
            )

        return response.text

    def create_pull_request_comment(
        self,
        repository_id: str,
        pull_request_id: int,
        content: str,
    ) -> dict:
        """
        Add a comment to a pull request.

        Args:
            repository_id: Repository ID
            pull_request_id: Pull request ID
            content: Comment content (markdown supported)

        Returns:
            Created comment details
        """
        # Create a thread with a comment
        payload = {
            "comments": [{"content": content, "commentType": 1}],  # 1 = text comment
            "status": 1,  # 1 = active
        }

        return self.post(
            f"repositories/{repository_id}/pullrequests/{pull_request_id}/threads",
            is_git_api=True,
            json=payload,
        )

    def push_changes(
        self,
        repository_id: str,
        branch: str,
        changes: list[dict],
        commit_message: str,
        old_object_id: str,
    ) -> dict:
        """
        Push changes to a branch.

        Args:
            repository_id: Repository ID
            branch: Branch name (without refs/heads/)
            changes: List of change objects
            commit_message: Commit message
            old_object_id: Current commit SHA of the branch

        Returns:
            Push result
        """
        payload = {
            "refUpdates": [
                {
                    "name": f"refs/heads/{branch}",
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": changes,
                }
            ],
        }

        return self.post(
            f"repositories/{repository_id}/pushes",
            is_git_api=True,
            json=payload,
        )
