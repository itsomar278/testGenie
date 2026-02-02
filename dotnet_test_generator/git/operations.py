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

    def __init__(self, repo_path: Path):
        """
        Initialize Git operations for an existing repository.

        Args:
            repo_path: Path to the Git repository
        """
        self.repo_path = repo_path
        try:
            self.repo = Repo(repo_path)
        except git.InvalidGitRepositoryError as e:
            raise GitOperationError(
                f"Invalid Git repository: {repo_path}",
                stderr=str(e),
            ) from e

    @classmethod
    def clone(cls, url: str, path: Path, branch: str | None = None) -> Self:
        """
        Clone a repository.

        Args:
            url: Repository URL (with authentication if needed)
            path: Local path for the clone
            branch: Branch to checkout (optional)

        Returns:
            GitOperations instance for the cloned repository
        """
        logger.info(f"Cloning to {path}")
        try:
            kwargs = {"depth": None}  # Full clone for history
            if branch:
                kwargs["branch"] = branch

            Repo.clone_from(url, path, **kwargs)
            return cls(path)

        except GitCommandError as e:
            # Sanitize error message to remove PAT
            error_msg = str(e.stderr) if e.stderr else str(e)
            if "@" in error_msg:
                error_msg = "Clone failed (credentials hidden)"

            raise GitOperationError(
                "Failed to clone repository",
                command="git clone",
                stderr=error_msg,
            ) from e

    def fetch_all(self) -> None:
        """Fetch all remote branches."""
        logger.debug("Fetching all remotes")
        try:
            for remote in self.repo.remotes:
                remote.fetch()
        except GitCommandError as e:
            raise GitOperationError(
                "Failed to fetch",
                command="git fetch",
                stderr=str(e.stderr),
            ) from e

    def checkout(self, branch: str, create: bool = False) -> None:
        """
        Checkout a branch.

        Args:
            branch: Branch name
            create: Create branch if it doesn't exist
        """
        logger.debug(f"Checking out branch: {branch}")
        try:
            if create:
                self.repo.git.checkout("-b", branch)
            else:
                # Try local branch first
                if branch in [b.name for b in self.repo.branches]:
                    self.repo.git.checkout(branch)
                else:
                    # Try remote branch
                    self.repo.git.checkout("-b", branch, f"origin/{branch}")

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
