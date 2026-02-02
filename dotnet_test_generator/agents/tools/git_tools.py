"""Git operation tools for agents."""

from pathlib import Path

from dotnet_test_generator.agents.tools.base import BaseTool, ToolResult
from dotnet_test_generator.git.operations import GitOperations
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


class GitDiffTool(BaseTool):
    """Tool to get git diff."""

    def __init__(self, git_ops: GitOperations):
        self.git_ops = git_ops

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return (
            "Get git diff output. Can compare current state with a branch/commit, "
            "or get diff for a specific file. Useful to see what changes have been made."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Reference to compare against (branch name or commit SHA)",
                },
                "path": {
                    "type": "string",
                    "description": "Optional file path to limit diff to",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        ref = kwargs.get("ref")
        path = kwargs.get("path")

        try:
            if path:
                diff = self.git_ops.diff_file(path, ref or "HEAD")
            else:
                diff = self.git_ops.diff(ref)

            if not diff:
                return ToolResult(
                    success=True,
                    output="No differences found",
                )

            return ToolResult(success=True, output=diff)

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class GitStatusTool(BaseTool):
    """Tool to get git status."""

    def __init__(self, git_ops: GitOperations):
        self.git_ops = git_ops

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return (
            "Get current git status showing modified, staged, and untracked files. "
            "Use this to see what files have been changed."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            status = self.git_ops.status()

            lines = []
            if status["staged"]:
                lines.append("Staged files:")
                for f in status["staged"]:
                    lines.append(f"  + {f}")

            if status["modified"]:
                lines.append("Modified files:")
                for f in status["modified"]:
                    lines.append(f"  M {f}")

            if status["untracked"]:
                lines.append("Untracked files:")
                for f in status["untracked"]:
                    lines.append(f"  ? {f}")

            output = "\n".join(lines) if lines else "Working tree clean"

            return ToolResult(
                success=True,
                output=output,
                data=status,
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class GitCheckoutTool(BaseTool):
    """Tool to checkout a branch."""

    def __init__(self, git_ops: GitOperations):
        self.git_ops = git_ops

    @property
    def name(self) -> str:
        return "git_checkout"

    @property
    def description(self) -> str:
        return (
            "Checkout a git branch. Can checkout existing branches or create new ones. "
            "Use carefully as this changes the working tree state."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Branch name to checkout",
                },
                "create": {
                    "type": "boolean",
                    "description": "Create the branch if it doesn't exist (default: false)",
                },
            },
            "required": ["branch"],
        }

    def execute(self, **kwargs) -> ToolResult:
        branch = kwargs.get("branch", "")
        create = kwargs.get("create", False)

        if not branch:
            return ToolResult(
                success=False,
                output="",
                error="Branch name is required",
            )

        try:
            self.git_ops.checkout(branch, create=create)
            current = self.git_ops.get_current_branch()

            return ToolResult(
                success=True,
                output=f"Switched to branch '{current}'",
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class GitLogTool(BaseTool):
    """Tool to get git log."""

    def __init__(self, git_ops: GitOperations):
        self.git_ops = git_ops

    @property
    def name(self) -> str:
        return "git_log"

    @property
    def description(self) -> str:
        return "Get recent git commit history."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of commits to show (default: 10)",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        count = kwargs.get("count", 10)

        try:
            commits = self.git_ops.log(count=count)

            lines = []
            for commit in commits:
                lines.append(f"{commit['sha'][:8]} {commit['message']}")

            return ToolResult(
                success=True,
                output="\n".join(lines),
                data={"commits": commits},
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class GitFileAtRefTool(BaseTool):
    """Tool to get file content at a specific git reference."""

    def __init__(self, git_ops: GitOperations):
        self.git_ops = git_ops

    @property
    def name(self) -> str:
        return "git_show_file"

    @property
    def description(self) -> str:
        return (
            "Get the content of a file at a specific git reference (branch, commit, or tag). "
            "Useful for comparing current content with a previous version."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                },
                "ref": {
                    "type": "string",
                    "description": "Git reference (branch name, commit SHA, or tag)",
                },
            },
            "required": ["path", "ref"],
        }

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        ref = kwargs.get("ref", "")

        if not path or not ref:
            return ToolResult(
                success=False,
                output="",
                error="Both path and ref are required",
            )

        try:
            content = self.git_ops.get_file_content_at_ref(path, ref)

            if content is None:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"File '{path}' not found at ref '{ref}'",
                )

            return ToolResult(
                success=True,
                output=content,
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
