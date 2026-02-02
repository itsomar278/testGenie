"""Solution analysis for .NET projects."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ProjectInfo:
    """Information about a .NET project."""

    name: str
    path: Path
    project_type: str  # classlib, web, console, test
    target_framework: str
    references: list[str] = field(default_factory=list)
    package_references: list[str] = field(default_factory=list)

    @property
    def is_test_project(self) -> bool:
        """Check if this is a test project."""
        return (
            self.project_type == "test" or
            "test" in self.name.lower() or
            "xunit" in [p.lower() for p in self.package_references]
        )


@dataclass
class SolutionInfo:
    """Information about a .NET solution."""

    name: str
    path: Path
    projects: list[ProjectInfo] = field(default_factory=list)

    @property
    def source_projects(self) -> list[ProjectInfo]:
        """Get non-test projects."""
        return [p for p in self.projects if not p.is_test_project]

    @property
    def test_projects(self) -> list[ProjectInfo]:
        """Get test projects."""
        return [p for p in self.projects if p.is_test_project]


class SolutionAnalyzer:
    """
    Analyzes .NET solutions and projects.

    Extracts project structure, references, and test mappings.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize analyzer.

        Args:
            repo_path: Path to repository root
        """
        self.repo_path = repo_path

    def find_solutions(self) -> list[Path]:
        """Find all solution files in the repository."""
        return list(self.repo_path.rglob("*.sln"))

    def find_projects(self) -> list[Path]:
        """Find all project files in the repository."""
        return list(self.repo_path.rglob("*.csproj"))

    def analyze_solution(self, solution_path: Path) -> SolutionInfo:
        """
        Analyze a solution file.

        Args:
            solution_path: Path to .sln file

        Returns:
            SolutionInfo with project details
        """
        logger.info(f"Analyzing solution: {solution_path}")

        solution_info = SolutionInfo(
            name=solution_path.stem,
            path=solution_path,
        )

        # Parse solution file to find projects
        try:
            content = solution_path.read_text()
            project_pattern = r'Project\("[^"]+"\)\s*=\s*"([^"]+)",\s*"([^"]+)"'

            for match in re.finditer(project_pattern, content):
                project_name = match.group(1)
                project_path_str = match.group(2)

                # Convert to absolute path
                project_path = solution_path.parent / project_path_str.replace("\\", "/")

                if project_path.exists() and project_path.suffix == ".csproj":
                    project_info = self.analyze_project(project_path)
                    solution_info.projects.append(project_info)

        except Exception as e:
            logger.error(f"Failed to parse solution: {e}")

        return solution_info

    def analyze_project(self, project_path: Path) -> ProjectInfo:
        """
        Analyze a project file.

        Args:
            project_path: Path to .csproj file

        Returns:
            ProjectInfo with project details
        """
        name = project_path.stem
        project_type = "classlib"
        target_framework = "net9.0"
        references = []
        package_references = []

        try:
            content = project_path.read_text()

            # Detect project type
            if re.search(r'<OutputType>Exe</OutputType>', content, re.IGNORECASE):
                project_type = "console"
            elif re.search(r'Sdk="Microsoft.NET.Sdk.Web"', content):
                project_type = "web"

            # Extract target framework
            tf_match = re.search(r'<TargetFramework>([^<]+)</TargetFramework>', content)
            if tf_match:
                target_framework = tf_match.group(1)

            # Extract project references
            for match in re.finditer(r'<ProjectReference\s+Include="([^"]+)"', content):
                ref_path = match.group(1).replace("\\", "/")
                ref_name = Path(ref_path).stem
                references.append(ref_name)

            # Extract package references
            for match in re.finditer(r'<PackageReference\s+Include="([^"]+)"', content):
                package_references.append(match.group(1))

            # Detect test project by packages
            test_packages = {"xunit", "nunit", "mstest", "microsoft.net.test.sdk"}
            if any(pkg.lower() in test_packages for pkg in package_references):
                project_type = "test"

        except Exception as e:
            logger.warning(f"Failed to parse project {project_path}: {e}")

        return ProjectInfo(
            name=name,
            path=project_path,
            project_type=project_type,
            target_framework=target_framework,
            references=references,
            package_references=package_references,
        )

    def find_test_project_for_source(
        self,
        source_project: ProjectInfo,
        solution: SolutionInfo,
    ) -> ProjectInfo | None:
        """
        Find the test project that corresponds to a source project.

        Args:
            source_project: Source project
            solution: Solution containing the project

        Returns:
            Test project or None
        """
        # Common naming conventions
        test_suffixes = [".Tests", ".Test", ".UnitTests", ".IntegrationTests"]

        for test_project in solution.test_projects:
            for suffix in test_suffixes:
                if test_project.name == source_project.name + suffix:
                    return test_project

            # Check if test project references source project
            if source_project.name in test_project.references:
                return test_project

        return None

    def get_project_structure(self, solution: SolutionInfo) -> dict:
        """
        Get a structured view of the solution.

        Args:
            solution: Solution to analyze

        Returns:
            Dictionary with project structure
        """
        return {
            "name": solution.name,
            "path": str(solution.path),
            "source_projects": [
                {
                    "name": p.name,
                    "type": p.project_type,
                    "framework": p.target_framework,
                    "path": str(p.path.relative_to(self.repo_path)),
                }
                for p in solution.source_projects
            ],
            "test_projects": [
                {
                    "name": p.name,
                    "type": p.project_type,
                    "framework": p.target_framework,
                    "path": str(p.path.relative_to(self.repo_path)),
                    "tests_for": [
                        ref for ref in p.references
                        if any(sp.name == ref for sp in solution.source_projects)
                    ],
                }
                for p in solution.test_projects
            ],
        }

    def ensure_test_project_exists(
        self,
        source_project: ProjectInfo,
        solution: SolutionInfo,
    ) -> tuple[bool, str]:
        """
        Check if a test project exists for a source project.

        Args:
            source_project: Source project to check
            solution: Solution containing the project

        Returns:
            Tuple of (exists, suggested_path)
        """
        test_project = self.find_test_project_for_source(source_project, solution)

        if test_project:
            return True, str(test_project.path)

        # Suggest a path for new test project
        suggested_name = f"{source_project.name}.Tests"
        suggested_path = self.repo_path / "tests" / suggested_name / f"{suggested_name}.csproj"

        return False, str(suggested_path)
