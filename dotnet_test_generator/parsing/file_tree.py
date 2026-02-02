"""File system tree generation."""

from dataclasses import dataclass, field
from pathlib import Path

from dotnet_test_generator.utils.logging import get_logger
from dotnet_test_generator.utils.json_utils import JsonHandler

logger = get_logger(__name__)


@dataclass
class FileNode:
    """Represents a file in the tree."""

    name: str
    path: str
    size: int
    extension: str


@dataclass
class DirectoryNode:
    """Represents a directory in the tree."""

    name: str
    path: str
    directories: list["DirectoryNode"] = field(default_factory=list)
    files: list[FileNode] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        """Count total files including subdirectories."""
        count = len(self.files)
        for subdir in self.directories:
            count += subdir.total_files
        return count


class FileTreeGenerator:
    """
    Generates file system tree structure.

    Creates a hierarchical representation of directories and files
    that can be used by agents to understand the repository structure.
    """

    # Default patterns to exclude
    DEFAULT_EXCLUDE_PATTERNS = {
        ".git",
        ".vs",
        ".idea",
        "bin",
        "obj",
        "node_modules",
        "__pycache__",
        ".venv",
        "packages",
        "TestResults",
    }

    # File extensions to focus on for .NET projects
    RELEVANT_EXTENSIONS = {
        ".cs",
        ".csproj",
        ".sln",
        ".json",
        ".xml",
        ".config",
        ".props",
        ".targets",
    }

    def __init__(
        self,
        exclude_patterns: set[str] | None = None,
        include_all_files: bool = False,
    ):
        """
        Initialize file tree generator.

        Args:
            exclude_patterns: Directory/file patterns to exclude
            include_all_files: Include all files, not just relevant extensions
        """
        self.exclude_patterns = exclude_patterns or self.DEFAULT_EXCLUDE_PATTERNS
        self.include_all_files = include_all_files

    def _should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded."""
        for pattern in self.exclude_patterns:
            if pattern in path.parts:
                return True
        return False

    def _should_include_file(self, file_path: Path) -> bool:
        """Check if file should be included."""
        if self.include_all_files:
            return True
        return file_path.suffix.lower() in self.RELEVANT_EXTENSIONS

    def generate_tree(self, root_path: Path) -> DirectoryNode:
        """
        Generate tree structure for a directory.

        Args:
            root_path: Root directory to scan

        Returns:
            DirectoryNode representing the tree
        """
        logger.info(f"Generating file tree for: {root_path}")
        return self._scan_directory(root_path, root_path)

    def _scan_directory(self, path: Path, root: Path) -> DirectoryNode:
        """Recursively scan a directory."""
        relative_path = str(path.relative_to(root)) if path != root else "."

        node = DirectoryNode(
            name=path.name or str(root),
            path=relative_path,
        )

        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

            for entry in entries:
                if self._should_exclude(entry):
                    continue

                if entry.is_dir():
                    subdir = self._scan_directory(entry, root)
                    # Only add non-empty directories
                    if subdir.files or subdir.directories:
                        node.directories.append(subdir)

                elif entry.is_file() and self._should_include_file(entry):
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0

                    node.files.append(FileNode(
                        name=entry.name,
                        path=str(entry.relative_to(root)),
                        size=size,
                        extension=entry.suffix.lower(),
                    ))

        except PermissionError:
            logger.warning(f"Permission denied: {path}")

        return node

    def to_dict(self, node: DirectoryNode) -> dict:
        """
        Convert tree to dictionary.

        Args:
            node: DirectoryNode to convert

        Returns:
            Dictionary representation
        """
        return {
            "name": node.name,
            "path": node.path,
            "directories": [self.to_dict(d) for d in node.directories],
            "files": [
                {
                    "name": f.name,
                    "path": f.path,
                    "size": f.size,
                    "extension": f.extension,
                }
                for f in node.files
            ],
        }

    def to_text(self, node: DirectoryNode, prefix: str = "", is_last: bool = True) -> str:
        """
        Convert tree to text representation.

        Args:
            node: DirectoryNode to convert
            prefix: Current line prefix
            is_last: Whether this is the last item at current level

        Returns:
            Text tree representation
        """
        lines = []

        # Current node
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{node.name}/")

        # Update prefix for children
        child_prefix = prefix + ("    " if is_last else "│   ")

        # All children (directories first, then files)
        all_children = [(True, d) for d in node.directories] + [(False, f) for f in node.files]

        for i, (is_dir, child) in enumerate(all_children):
            is_last_child = i == len(all_children) - 1

            if is_dir:
                lines.append(self.to_text(child, child_prefix, is_last_child))
            else:
                connector = "└── " if is_last_child else "├── "
                lines.append(f"{child_prefix}{connector}{child.name}")

        return "\n".join(lines)

    def save_tree(self, tree: DirectoryNode, output_path: Path) -> None:
        """
        Save tree to JSON file.

        Args:
            tree: Tree to save
            output_path: Output file path
        """
        tree_dict = self.to_dict(tree)
        JsonHandler.dump_file(tree_dict, output_path)
        logger.info(f"Saved file tree to {output_path}")

    def get_all_file_paths(self, node: DirectoryNode) -> list[str]:
        """
        Get flat list of all file paths.

        Args:
            node: DirectoryNode to traverse

        Returns:
            List of file paths
        """
        paths = [f.path for f in node.files]
        for subdir in node.directories:
            paths.extend(self.get_all_file_paths(subdir))
        return paths

    def find_files_by_extension(
        self,
        node: DirectoryNode,
        extension: str,
    ) -> list[str]:
        """
        Find all files with a specific extension.

        Args:
            node: DirectoryNode to search
            extension: Extension to match (with dot, e.g., ".cs")

        Returns:
            List of matching file paths
        """
        extension = extension.lower()
        matches = [f.path for f in node.files if f.extension == extension]
        for subdir in node.directories:
            matches.extend(self.find_files_by_extension(subdir, extension))
        return matches

    def find_files_in_directory(
        self,
        node: DirectoryNode,
        directory_name: str,
    ) -> list[str]:
        """
        Find all files under directories with a specific name.

        Args:
            node: DirectoryNode to search
            directory_name: Directory name to match (e.g., "src", "tests")

        Returns:
            List of file paths under matching directories
        """
        matches = []

        if node.name.lower() == directory_name.lower():
            # Return all files under this directory
            return self.get_all_file_paths(node)

        for subdir in node.directories:
            matches.extend(self.find_files_in_directory(subdir, directory_name))

        return matches

    def get_project_structure_summary(self, tree: DirectoryNode) -> dict:
        """
        Get a summary of the project structure.

        Args:
            tree: File tree

        Returns:
            Summary dictionary with counts and key paths
        """
        all_files = self.get_all_file_paths(tree)
        cs_files = self.find_files_by_extension(tree, ".cs")
        csproj_files = self.find_files_by_extension(tree, ".csproj")
        sln_files = self.find_files_by_extension(tree, ".sln")

        src_files = self.find_files_in_directory(tree, "src")
        test_files = self.find_files_in_directory(tree, "tests")

        return {
            "total_files": len(all_files),
            "csharp_files": len(cs_files),
            "projects": csproj_files,
            "solutions": sln_files,
            "src_directory": {
                "files": len(src_files),
                "csharp_files": len([f for f in src_files if f.endswith(".cs")]),
            },
            "tests_directory": {
                "files": len(test_files),
                "csharp_files": len([f for f in test_files if f.endswith(".cs")]),
            },
        }
