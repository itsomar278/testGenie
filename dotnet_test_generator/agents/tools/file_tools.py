"""File operation tools for agents."""

import os
import shutil
from pathlib import Path

from dotnet_test_generator.agents.tools.base import BaseTool, ToolResult
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


class ReadFileTool(BaseTool):
    """Tool to read file contents."""

    def __init__(self, base_path: Path):
        """
        Initialize tool.

        Args:
            base_path: Base path for file operations (repository root)
        """
        self.base_path = base_path

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Provide the file path relative to the repository root. "
            "Returns the file contents as text."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repository root",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional start line number (1-indexed)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional end line number (1-indexed, inclusive)",
                },
            },
            "required": ["path"],
        }

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        start_line = kwargs.get("start_line")
        end_line = kwargs.get("end_line")

        if not path:
            return ToolResult(success=False, output="", error="Path is required")

        file_path = self.base_path / path.replace("\\", "/")

        if not file_path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"File not found: {path}",
            )

        if not file_path.is_file():
            return ToolResult(
                success=False,
                output="",
                error=f"Not a file: {path}",
            )

        # Security check - ensure path is within base
        try:
            file_path.resolve().relative_to(self.base_path.resolve())
        except ValueError:
            return ToolResult(
                success=False,
                output="",
                error="Path is outside repository",
            )

        try:
            content = file_path.read_text(encoding="utf-8-sig")

            # Handle line range
            if start_line is not None or end_line is not None:
                lines = content.split("\n")
                start = (start_line or 1) - 1
                end = end_line or len(lines)
                content = "\n".join(lines[start:end])

            return ToolResult(
                success=True,
                output=content,
                data={"path": path, "lines": len(content.split("\n"))},
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class WriteFileTool(BaseTool):
    """Tool to write file contents."""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file and parent directories if they don't exist. "
            "Overwrites existing content. Use this to create new test files or modify existing ones."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repository root",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")

        if not path:
            return ToolResult(success=False, output="", error="Path is required")

        file_path = self.base_path / path.replace("\\", "/")

        # Security check
        try:
            file_path.resolve().relative_to(self.base_path.resolve())
        except ValueError:
            return ToolResult(
                success=False,
                output="",
                error="Path is outside repository",
            )

        try:
            # Create parent directories
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            file_path.write_text(content, encoding="utf-8")

            logger.info(f"Wrote file: {path}")
            return ToolResult(
                success=True,
                output=f"Successfully wrote {len(content)} bytes to {path}",
                data={"path": path, "bytes": len(content)},
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class DeleteFileTool(BaseTool):
    """Tool to delete files."""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    @property
    def name(self) -> str:
        return "delete_file"

    @property
    def description(self) -> str:
        return "Delete a file. Use this to remove test files that are no longer needed."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repository root",
                },
            },
            "required": ["path"],
        }

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")

        if not path:
            return ToolResult(success=False, output="", error="Path is required")

        file_path = self.base_path / path.replace("\\", "/")

        # Security check
        try:
            file_path.resolve().relative_to(self.base_path.resolve())
        except ValueError:
            return ToolResult(
                success=False,
                output="",
                error="Path is outside repository",
            )

        if not file_path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"File not found: {path}",
            )

        try:
            file_path.unlink()
            logger.info(f"Deleted file: {path}")
            return ToolResult(
                success=True,
                output=f"Successfully deleted {path}",
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class RenameFileTool(BaseTool):
    """Tool to rename/move files."""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    @property
    def name(self) -> str:
        return "rename_file"

    @property
    def description(self) -> str:
        return (
            "Rename or move a file. Creates destination directories if needed. "
            "Can be used to reorganize test files."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Current file path relative to repository root",
                },
                "dest_path": {
                    "type": "string",
                    "description": "New file path relative to repository root",
                },
            },
            "required": ["source_path", "dest_path"],
        }

    def execute(self, **kwargs) -> ToolResult:
        source = kwargs.get("source_path", "")
        dest = kwargs.get("dest_path", "")

        if not source or not dest:
            return ToolResult(
                success=False,
                output="",
                error="Both source_path and dest_path are required",
            )

        source_path = self.base_path / source.replace("\\", "/")
        dest_path = self.base_path / dest.replace("\\", "/")

        # Security checks
        try:
            source_path.resolve().relative_to(self.base_path.resolve())
            dest_path.resolve().relative_to(self.base_path.resolve())
        except ValueError:
            return ToolResult(
                success=False,
                output="",
                error="Path is outside repository",
            )

        if not source_path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"Source file not found: {source}",
            )

        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(dest_path))

            logger.info(f"Renamed file: {source} -> {dest}")
            return ToolResult(
                success=True,
                output=f"Successfully renamed {source} to {dest}",
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class ListDirectoryTool(BaseTool):
    """Tool to list directory contents."""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    @property
    def name(self) -> str:
        return "list_directory"

    @property
    def description(self) -> str:
        return (
            "List contents of a directory. Returns files and subdirectories. "
            "Use this to explore the repository structure."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to repository root (empty for root)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List recursively (default: false)",
                },
                "pattern": {
                    "type": "string",
                    "description": "Filter by glob pattern (e.g., '*.cs')",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", "") or "."
        recursive = kwargs.get("recursive", False)
        pattern = kwargs.get("pattern")

        dir_path = self.base_path / path.replace("\\", "/")

        # Security check
        try:
            dir_path.resolve().relative_to(self.base_path.resolve())
        except ValueError:
            return ToolResult(
                success=False,
                output="",
                error="Path is outside repository",
            )

        if not dir_path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"Directory not found: {path}",
            )

        if not dir_path.is_dir():
            return ToolResult(
                success=False,
                output="",
                error=f"Not a directory: {path}",
            )

        try:
            entries = []

            if recursive:
                if pattern:
                    files = dir_path.rglob(pattern)
                else:
                    files = dir_path.rglob("*")
            else:
                if pattern:
                    files = dir_path.glob(pattern)
                else:
                    files = dir_path.iterdir()

            for entry in sorted(files):
                rel_path = entry.relative_to(self.base_path)
                entry_type = "dir" if entry.is_dir() else "file"
                entries.append(f"[{entry_type}] {rel_path}")

            output = "\n".join(entries[:500])  # Limit output
            if len(entries) > 500:
                output += f"\n... and {len(entries) - 500} more"

            return ToolResult(
                success=True,
                output=output,
                data={"count": len(entries)},
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class SearchFilesTool(BaseTool):
    """Tool to search for files by name or content."""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return (
            "Search for files by name pattern or content. "
            "Use this to find specific files in the repository."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern for file names (e.g., '*Service*.cs')",
                },
                "content": {
                    "type": "string",
                    "description": "Search for files containing this text",
                },
                "directory": {
                    "type": "string",
                    "description": "Directory to search in (default: entire repository)",
                },
            },
            "required": [],
        }

    def execute(self, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern", "*")
        content = kwargs.get("content")
        directory = kwargs.get("directory", "")

        search_path = self.base_path / directory.replace("\\", "/") if directory else self.base_path

        # Security check
        try:
            search_path.resolve().relative_to(self.base_path.resolve())
        except ValueError:
            return ToolResult(
                success=False,
                output="",
                error="Path is outside repository",
            )

        try:
            matches = []

            for file_path in search_path.rglob(pattern):
                if not file_path.is_file():
                    continue

                # Skip binary/large files
                if file_path.suffix.lower() in {".dll", ".exe", ".bin", ".pdb"}:
                    continue

                rel_path = str(file_path.relative_to(self.base_path))

                if content:
                    try:
                        file_content = file_path.read_text(encoding="utf-8-sig")
                        if content.lower() in file_content.lower():
                            matches.append(rel_path)
                    except Exception:
                        pass
                else:
                    matches.append(rel_path)

                if len(matches) >= 100:
                    break

            output = "\n".join(matches)
            if len(matches) >= 100:
                output += "\n... (limited to 100 results)"

            return ToolResult(
                success=True,
                output=output if matches else "No files found",
                data={"count": len(matches)},
            )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
