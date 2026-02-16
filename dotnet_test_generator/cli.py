"""Command-line interface for the test generator."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from dotnet_test_generator import __version__
from dotnet_test_generator.config import (
    Settings,
    AzureDevOpsSettings,
    OllamaSettings,
    WorkflowSettings,
    LoggingSettings,
    configure_settings,
)
from dotnet_test_generator.core.workflow import TestGenerationWorkflow, WorkflowResult
from dotnet_test_generator.agents.ollama_client import OllamaClient
from dotnet_test_generator.utils.logging import setup_logging

console = Console(force_terminal=True)


def print_banner():
    """Print application banner."""
    banner = """
+-----------------------------------------------------------+
|     .NET Test Generator - AI-Powered Test Generation      |
|                   Powered by Qwen Coder 3                 |
+-----------------------------------------------------------+
    """
    console.print(banner, style="bold blue")


def print_result(result: WorkflowResult):
    """Print workflow result summary."""
    status = "[green]SUCCESS[/green]" if result.success else "[red]FAILED[/red]"

    table = Table(title="Test Generation Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Status", status)
    table.add_row("Tests Created", str(result.tests_created))
    table.add_row("Tests Modified", str(result.tests_modified))
    table.add_row("Tests Deleted", str(result.tests_deleted))
    table.add_row("Build Success", "Yes" if result.build_success else "No")

    if result.test_summary:
        table.add_row("Total Tests", str(result.test_summary.get("total", 0)))
        table.add_row("Passed", str(result.test_summary.get("passed", 0)))
        table.add_row("Failed", str(result.test_summary.get("failed", 0)))

    if result.commit_sha:
        table.add_row("Commit", result.commit_sha[:8])

    console.print(table)

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for error in result.errors:
            console.print(f"  -{error}")


@click.group()
@click.version_option(version=__version__)
def main():
    """AI-Driven .NET Test Generation System for Azure DevOps."""
    pass


@main.command()
@click.argument("repository_url")
@click.argument("pull_request_id", type=int)
@click.option(
    "--pat",
    envvar="AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN",
    help="Azure DevOps Personal Access Token",
    required=True,
)
@click.option(
    "--organization",
    envvar="AZURE_DEVOPS_ORGANIZATION_URL",
    help="Azure DevOps organization URL",
    required=True,
)
@click.option(
    "--project",
    envvar="AZURE_DEVOPS_PROJECT",
    help="Azure DevOps project name",
    required=True,
)
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    envvar="OLLAMA_BASE_URL",
    help="Ollama server URL",
)
@click.option(
    "--model",
    default="qwen2.5-coder:32b",
    envvar="OLLAMA_MODEL",
    help="Ollama model to use",
)
@click.option(
    "--work-dir",
    default="./workdir",
    type=click.Path(),
    help="Working directory for cloned repositories",
)
@click.option(
    "--max-iterations",
    default=10,
    type=int,
    help="Maximum build fix iterations",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--only-build",
    is_flag=True,
    envvar="ONLY_BUILD",
    help="Skip test generation and only verify the build",
)
def generate(
    repository_url: str,
    pull_request_id: int,
    pat: str,
    organization: str,
    project: str,
    ollama_url: str,
    model: str,
    work_dir: str,
    max_iterations: int,
    verbose: bool,
    only_build: bool,
):
    """
    Generate tests for a pull request.

    REPOSITORY_URL: Azure DevOps repository URL
    PULL_REQUEST_ID: Pull request number
    """
    print_banner()

    # Configure settings
    settings = Settings(
        azure_devops=AzureDevOpsSettings(
            organization_url=organization,
            personal_access_token=pat,
            project=project,
        ),
        ollama=OllamaSettings(
            base_url=ollama_url,
            model=model,
        ),
        workflow=WorkflowSettings(
            work_directory=Path(work_dir),
            max_build_fix_iterations=max_iterations,
            only_build=only_build,
        ),
        logging=LoggingSettings(
            level="DEBUG" if verbose else "INFO",
        ),
    )

    configure_settings(settings)

    console.print(f"\n[bold]Repository:[/bold] {repository_url}")
    console.print(f"[bold]Pull Request:[/bold] #{pull_request_id}")
    if only_build:
        console.print("[bold yellow]Mode:[/bold yellow] Build only (test generation skipped)\n")
    else:
        console.print(f"[bold]Model:[/bold] {model}\n")

    # Run workflow
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        description = "Running build verification..." if only_build else "Running test generation workflow..."
        task = progress.add_task(description, total=None)

        workflow = TestGenerationWorkflow(settings)
        result = workflow.run(repository_url, pull_request_id)

        progress.update(task, completed=True)

    # Print results
    print_result(result)

    sys.exit(0 if result.success else 1)


@main.command()
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    envvar="OLLAMA_BASE_URL",
    help="Ollama server URL",
)
def check(ollama_url: str):
    """Check if Ollama is available and list models."""
    console.print("[bold]Checking Ollama connection...[/bold]\n")

    client = OllamaClient(base_url=ollama_url)

    if client.is_available():
        console.print("[green][OK][/green] Ollama server is available")

        models = client.list_models()
        if models:
            console.print(f"\n[bold]Available models ({len(models)}):[/bold]")
            for model in models:
                console.print(f"  -{model}")
        else:
            console.print("\n[yellow]No models found[/yellow]")
    else:
        console.print("[red][FAIL][/red] Ollama server is not available")
        console.print(f"  Tried: {ollama_url}")
        sys.exit(1)


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for the index",
)
def index(path: str, output: str | None):
    """
    Index a .NET repository.

    PATH: Path to the repository
    """
    from dotnet_test_generator.parsing.csharp_parser import CSharpParser
    from dotnet_test_generator.parsing.file_tree import FileTreeGenerator
    from dotnet_test_generator.utils.json_utils import JsonHandler

    repo_path = Path(path)

    console.print(f"[bold]Indexing repository:[/bold] {repo_path}\n")

    # Generate file tree
    with console.status("Generating file tree..."):
        tree_gen = FileTreeGenerator()
        tree = tree_gen.generate_tree(repo_path)

    summary = tree_gen.get_project_structure_summary(tree)
    console.print(f"[green][OK][/green] Found {summary['total_files']} files")
    console.print(f"  C# files: {summary['csharp_files']}")
    console.print(f"  Projects: {len(summary['projects'])}")

    # Parse C# files
    with console.status("Parsing C# files..."):
        parser = CSharpParser()
        results = parser.parse_directory(repo_path)

    console.print(f"[green][OK][/green] Parsed {len(results)} C# files")

    # Create index
    index_data = parser.get_searchable_index(results)
    console.print(f"  Classes: {len(index_data['classes'])}")
    console.print(f"  Methods: {len(index_data['methods'])}")

    if output:
        output_path = Path(output)
        JsonHandler.dump_file(
            {
                "file_tree": tree_gen.to_dict(tree),
                "parse_results": results,
                "index": index_data,
            },
            output_path,
        )
        console.print(f"\n[green][OK][/green] Saved index to {output_path}")


@main.command()
@click.argument("path", type=click.Path(exists=True))
def analyze(path: str):
    """
    Analyze a .NET solution structure.

    PATH: Path to the repository or solution file
    """
    from dotnet_test_generator.dotnet.solution import SolutionAnalyzer

    repo_path = Path(path)
    analyzer = SolutionAnalyzer(repo_path)

    # Find solutions
    solutions = analyzer.find_solutions()

    if not solutions:
        console.print("[yellow]No solution files found[/yellow]")
        return

    for sln_path in solutions:
        console.print(f"\n[bold]Solution:[/bold] {sln_path.name}")

        solution = analyzer.analyze_solution(sln_path)
        structure = analyzer.get_project_structure(solution)

        # Source projects
        if structure["source_projects"]:
            console.print("\n[cyan]Source Projects:[/cyan]")
            for proj in structure["source_projects"]:
                console.print(f"  -{proj['name']} ({proj['type']}) - {proj['framework']}")

        # Test projects
        if structure["test_projects"]:
            console.print("\n[cyan]Test Projects:[/cyan]")
            for proj in structure["test_projects"]:
                tests_for = ", ".join(proj["tests_for"]) if proj["tests_for"] else "unknown"
                console.print(f"  -{proj['name']} (tests: {tests_for})")


if __name__ == "__main__":
    main()
