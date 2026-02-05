"""Git operations for repository management."""

from pathlib import Path
from typing import Self

import git
from git import Repo, GitCommandError

from dotnet_test_generator.core.exceptions import GitOperationError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


class GitOperations:
    """
    Wrapper for Git operations using GitPython.

    Provides a clean interface for common Git operations needed
    by the test generation workflow.
    """

    def __init__(self, repo_path: Path, extra_config: list[str] | None = None):
        """
        Initialize Git operations for an existing repository.

        Args:
            repo_path: Path to the Git repository
            extra_config: Extra git config options for authenticated operations
        """
        self.repo_path = repo_path
        self.extra_config = extra_config or []
        try:
            self.repo = Repo(repo_path)
        except git.InvalidGitRepositoryError as e:
            raise GitOperationError(
                f"Invalid Git repository: {repo_path}",
                stderr=str(e),
            ) from e

    @classmethod
    def clone(cls, url: str, path: Path, branch: str | None = None, extra_config: list[str] | None = None) -> Self:
        """
        Clone a repository.

        Args:
            url: Repository URL (with authentication if needed)
            path: Local path for the clone
            branch: Branch to checkout (optional)
            extra_config: Extra git config options (e.g., ['http.extraheader=...'])

        Returns:
            GitOperations instance for the cloned repository
        """
        import os
        import subprocess

        logger.info(f"[GIT] Cloning to {path}")
        logger.info(f"[GIT] Branch: {branch or 'default'}")

        # Check for proxy settings
        http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')

        if http_proxy:
            logger.info(f"[GIT] HTTP Proxy: {http_proxy}")
        if https_proxy:
            logger.info(f"[GIT] HTTPS Proxy: {https_proxy}")

        try:
            # Build git clone command
            cmd = ['git']

            # Add config options
            if http_proxy:
                cmd.extend(['-c', f'http.proxy={http_proxy}'])
            if https_proxy:
                cmd.extend(['-c', f'https.proxy={https_proxy}'])

            # Add extra config (like auth headers)
            if extra_config:
                for cfg in extra_config:
                    cmd.extend(['-c', cfg])
                logger.info(f"[GIT] Using extraheader for authentication")

            cmd.append('clone')

            if branch:
                cmd.extend(['-b', branch])

            cmd.extend([url, str(path)])

            logger.info(f"[GIT] Running git clone (config options: {len([c for c in cmd if c == '-c'])})")
            logger.debug(f"[GIT] Command length: {len(cmd)} args")

            # Run git clone using subprocess for full control
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                # Sanitize credentials from error
                if "AUTHORIZATION" in error_msg or "@" in error_msg:
                    logger.error(f"[GIT] Clone failed (credentials hidden in logs)")
                else:
                    logger.error(f"[GIT] Clone error: {error_msg[:300]}")
                raise GitOperationError(
                    "Failed to clone repository",
                    command="git clone",
                    stderr=error_msg if "AUTHORIZATION" not in error_msg else "Clone failed (credentials hidden)",
                )

            logger.info(f"[GIT] Clone successful")
            return cls(path, extra_config=extra_config)

        except subprocess.TimeoutExpired:
            raise GitOperationError(
                "Failed to clone repository",
                command="git clone",
                stderr="Clone timed out after 600 seconds",
            )
        except GitOperationError:
            raise
        except Exception as e:
            raise GitOperationError(
                "Failed to clone repository",
                command="git clone",
                stderr=str(e),
            ) from e

    def fetch_all(self) -> None:
        """Fetch all remote branches."""
        import os
        import subprocess

        logger.info("[GIT] Fetching all remotes")

        # Check for proxy settings
        http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')

        try:
            for remote in self.repo.remotes:
                logger.info(f"[GIT] Fetching remote: {remote.name}")

                # Build git fetch command with auth config
                cmd = ['git', '-C', str(self.repo_path)]

                # Add proxy config
                if http_proxy:
                    cmd.extend(['-c', f'http.proxy={http_proxy}'])
                if https_proxy:
                    cmd.extend(['-c', f'https.proxy={https_proxy}'])

                # Add extra config (like auth headers)
                if self.extra_config:
                    for cfg in self.extra_config:
                        cmd.extend(['-c', cfg])
                    logger.info("[GIT] Using extraheader for authentication")

                cmd.extend(['fetch', remote.name])

                logger.debug(f"[GIT] Fetch command args count: {len(cmd)}")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "Unknown error"
                    # Sanitize credentials from error
                    if "AUTHORIZATION" in error_msg or "@" in error_msg:
                        logger.error("[GIT] Fetch failed (credentials hidden in logs)")
                        error_msg = "Fetch failed (credentials hidden)"
                    else:
                        logger.error(f"[GIT] Fetch error: {error_msg[:300]}")
                    raise GitOperationError(
                        "Failed to fetch",
                        command="git fetch",
                        stderr=error_msg,
                    )

                logger.info(f"[GIT] Fetch successful for {remote.name}")

        except subprocess.TimeoutExpired:
            raise GitOperationError(
                "Failed to fetch",
                command="git fetch",
                stderr="Fetch timed out after 300 seconds",
            )
        except GitOperationError:
            raise
        except Exception as e:
            raise GitOperationError(
                "Failed to fetch",
                command="git fetch",
                stderr=str(e),
            ) from e

    def checkout(self, branch: str, create: bool = False) -> None:
        """
        Checkout a branch.

        Args:
            branch: Branch name
            create: Create branch if it doesn't exist
        """
        import subprocess

        logger.info(f"[GIT] Checking out branch: {branch}")
        try:
            if create:
                logger.info(f"[GIT] Creating new branch: {branch}")
                self.repo.git.checkout("-b", branch)
            else:
                # Try local branch first
                if branch in [b.name for b in self.repo.branches]:
                    logger.info(f"[GIT] Switching to local branch: {branch}")
                    self.repo.git.checkout(branch)
                else:
                    # Try remote branch - use subprocess for auth
                    logger.info(f"[GIT] Creating local branch from origin/{branch}")
                    cmd = ['git', '-C', str(self.repo_path), 'checkout', '-b', branch, f'origin/{branch}']
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode != 0:
                        error_msg = result.stderr or result.stdout or "Unknown error"
                        logger.error(f"[GIT] Checkout error: {error_msg}")
                        raise GitOperationError(
                            f"Failed to checkout branch: {branch}",
                            command=f"git checkout -b {branch} origin/{branch}",
                            stderr=error_msg,
                        )
                    logger.info(f"[GIT] Successfully checked out {branch}")

        except GitOperationError:
            raise
        except GitCommandError as e:
            raise GitOperationError(
                f"Failed to checkout branch: {branch}",
                command=f"git checkout {branch}",
                stderr=str(e.stderr),
            ) from e

    def get_current_branch(self) -> str:
        """Get the current branch name."""
        try:
            return self.repo.active_branch.name
        except TypeError:
            # Detached HEAD state
            return self.repo.head.commit.hexsha[:8]

    def get_current_commit(self) -> str:
        """Get the current commit SHA."""
        return self.repo.head.commit.hexsha

    def status(self) -> dict:
        """
        Get repository status.

        Returns:
            Dict with modified, added, deleted, and untracked files
        """
        return {
            "modified": [item.a_path for item in self.repo.index.diff(None)],
            "staged": [item.a_path for item in self.repo.index.diff("HEAD")],
            "untracked": self.repo.untracked_files,
        }

    def diff(
        self,
        ref1: str | None = None,
        ref2: str | None = None,
        path: str | None = None,
    ) -> str:
        """
        Get diff output.

        Args:
            ref1: First reference (commit, branch)
            ref2: Second reference
            path: Optional path to limit diff

        Returns:
            Diff output as string
        """
        try:
            args = []
            if ref1:
                args.append(ref1)
            if ref2:
                args.append(ref2)
            if path:
                args.extend(["--", path])

            return self.repo.git.diff(*args)

        except GitCommandError as e:
            raise GitOperationError(
                "Failed to get diff",
                command=f"git diff {' '.join(args)}",
                stderr=str(e.stderr),
            ) from e

    def diff_file(self, file_path: str, ref: str = "HEAD") -> str:
        """
        Get diff for a specific file against a reference.

        Args:
            file_path: Path to file
            ref: Reference to compare against

        Returns:
            Diff output
        """
        return self.diff(ref, path=file_path)

    def get_file_content_at_ref(self, file_path: str, ref: str) -> str | None:
        """
        Get file content at a specific reference.

        Args:
            file_path: Path to file
            ref: Git reference (commit, branch, tag)

        Returns:
            File content or None if file doesn't exist
        """
        try:
            return self.repo.git.show(f"{ref}:{file_path}")
        except GitCommandError:
            return None

    def add(self, paths: list[str] | str) -> None:
        """
        Stage files for commit.

        Args:
            paths: File path(s) to stage
        """
        if isinstance(paths, str):
            paths = [paths]

        logger.debug(f"Staging files: {paths}")
        try:
            self.repo.index.add(paths)
        except GitCommandError as e:
            raise GitOperationError(
                "Failed to stage files",
                command=f"git add {' '.join(paths)}",
                stderr=str(e.stderr),
            ) from e

    def add_all(self) -> None:
        """Stage all changes."""
        logger.debug("Staging all changes")
        self.repo.git.add("-A")

    def commit(self, message: str) -> str:
        """
        Create a commit.

        Args:
            message: Commit message

        Returns:
            Commit SHA
        """
        logger.debug(f"Creating commit: {message[:50]}...")
        try:
            self.repo.index.commit(message)
            return self.repo.head.commit.hexsha

        except GitCommandError as e:
            raise GitOperationError(
                "Failed to commit",
                command="git commit",
                stderr=str(e.stderr),
            ) from e

    def push(self, branch: str | None = None, force: bool = False) -> None:
        """
        Push commits to remote.

        Args:
            branch: Branch to push (defaults to current)
            force: Force push
        """
        branch = branch or self.get_current_branch()
        logger.info(f"Pushing to origin/{branch}")

        try:
            origin = self.repo.remote("origin")
            push_args = [branch]
            if force:
                push_args.insert(0, "--force")

            origin.push(refspec=f"{branch}:{branch}", force=force)

        except GitCommandError as e:
            error_msg = str(e.stderr) if e.stderr else str(e)
            if "@" in error_msg:
                error_msg = "Push failed (credentials hidden)"

            raise GitOperationError(
                f"Failed to push to {branch}",
                command="git push",
                stderr=error_msg,
            ) from e

    def reset_hard(self, ref: str = "HEAD") -> None:
        """
        Hard reset to a reference.

        Args:
            ref: Reference to reset to
        """
        logger.warning(f"Hard reset to {ref}")
        try:
            self.repo.git.reset("--hard", ref)
        except GitCommandError as e:
            raise GitOperationError(
                f"Failed to reset to {ref}",
                command=f"git reset --hard {ref}",
                stderr=str(e.stderr),
            ) from e

    def clean(self, directories: bool = True, force: bool = True) -> None:
        """
        Clean untracked files.

        Args:
            directories: Also remove untracked directories
            force: Force clean
        """
        logger.warning("Cleaning untracked files")
        args = []
        if force:
            args.append("-f")
        if directories:
            args.append("-d")

        try:
            self.repo.git.clean(*args)
        except GitCommandError as e:
            raise GitOperationError(
                "Failed to clean",
                command=f"git clean {' '.join(args)}",
                stderr=str(e.stderr),
            ) from e

    def get_changed_files(self, base_ref: str, head_ref: str = "HEAD") -> list[str]:
        """
        Get list of files changed between two references.

        Args:
            base_ref: Base reference
            head_ref: Head reference

        Returns:
            List of changed file paths
        """
        try:
            diff_output = self.repo.git.diff("--name-only", base_ref, head_ref)
            return [f for f in diff_output.split("\n") if f.strip()]
        except GitCommandError as e:
            raise GitOperationError(
                "Failed to get changed files",
                command=f"git diff --name-only {base_ref} {head_ref}",
                stderr=str(e.stderr),
            ) from e

    def log(self, count: int = 10, format_str: str = "%H %s") -> list[dict]:
        """
        Get commit log.

        Args:
            count: Number of commits to retrieve
            format_str: Git log format string

        Returns:
            List of commit info dicts
        """
        try:
            log_output = self.repo.git.log(f"-{count}", f"--format={format_str}")
            commits = []
            for line in log_output.split("\n"):
                if line.strip():
                    parts = line.split(" ", 1)
                    commits.append({
                        "sha": parts[0],
                        "message": parts[1] if len(parts) > 1 else "",
                    })
            return commits

        except GitCommandError as e:
            raise GitOperationError(
                "Failed to get log",
                command="git log",
                stderr=str(e.stderr),
            ) from e
