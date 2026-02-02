"""Main workflow orchestrator for test generation."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotnet_test_generator.config import Settings, get_settings
from dotnet_test_generator.azure_devops.client import AzureDevOpsClient
from dotnet_test_generator.azure_devops.repository import RepositoryManager, RepositoryInfo
from dotnet_test_generator.azure_devops.pull_request import (
    PullRequestManager,
    PullRequestInfo,
    ChangeType,
)
from dotnet_test_generator.git.operations import GitOperations
from dotnet_test_generator.parsing.csharp_parser import CSharpParser
from dotnet_test_generator.parsing.file_tree import FileTreeGenerator
from dotnet_test_generator.parsing.change_detector import ChangeDetector, ChangeAnalysis
from dotnet_test_generator.agents.ollama_client import OllamaClient
from dotnet_test_generator.agents.test_generator import (
    TestGeneratorOrchestrator,
    TestGenerationResult,
)
from dotnet_test_generator.agents.build_fixer import BuildFixOrchestrator
from dotnet_test_generator.dotnet.solution import SolutionAnalyzer
from dotnet_test_generator.dotnet.builder import SolutionBuilder
from dotnet_test_generator.dotnet.test_runner import TestRunner
from dotnet_test_generator.utils.logging import get_logger, setup_logging
from dotnet_test_generator.utils.json_utils import JsonHandler

logger = get_logger(__name__)


@dataclass
class WorkflowResult:
    """Result of the complete workflow."""

    success: bool
    repository: str
    pull_request_id: int
    tests_created: int
    tests_modified: int
    tests_deleted: int
    build_success: bool
    test_summary: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    commit_sha: str | None = None


class TestGenerationWorkflow:
    """
    Main workflow orchestrator.

    Coordinates the entire test generation process from PR detection
    to committing changes back.
    """

    def __init__(self, settings: Settings | None = None):
        """
        Initialize workflow.

        Args:
            settings: Configuration settings (uses defaults if not provided)
        """
        self.settings = settings or get_settings()

        # Initialize logging
        setup_logging(
            level=self.settings.logging.level,
            log_format=self.settings.logging.format,
            log_file=self.settings.logging.file,
            rich_console=self.settings.logging.rich_console,
        )

        # Initialize clients
        self.ado_client: AzureDevOpsClient | None = None
        self.ollama_client: OllamaClient | None = None

        # Workflow state
        self.repo_path: Path | None = None
        self.repo_info: RepositoryInfo | None = None
        self.pr_info: PullRequestInfo | None = None
        self.git_ops: GitOperations | None = None

    def _init_azure_client(self) -> AzureDevOpsClient:
        """Initialize Azure DevOps client."""
        if self.ado_client is None:
            self.ado_client = AzureDevOpsClient(
                organization_url=self.settings.azure_devops.organization_url,
                personal_access_token=self.settings.azure_devops.personal_access_token.get_secret_value(),
                project=self.settings.azure_devops.project,
            )
        return self.ado_client

    def _init_ollama_client(self) -> OllamaClient:
        """Initialize Ollama client."""
        if self.ollama_client is None:
            self.ollama_client = OllamaClient(
                base_url=self.settings.ollama.base_url,
                model=self.settings.ollama.model,
                timeout=self.settings.ollama.timeout,
                num_ctx=self.settings.ollama.num_ctx,
                temperature=self.settings.ollama.temperature,
            )
        return self.ollama_client

    def run(
        self,
        repository_url: str,
        pull_request_id: int,
    ) -> WorkflowResult:
        """
        Execute the complete test generation workflow.

        Args:
            repository_url: Azure DevOps repository URL
            pull_request_id: Pull request ID

        Returns:
            WorkflowResult with outcome details
        """
        logger.info(f"Starting test generation workflow for PR #{pull_request_id}")

        result = WorkflowResult(
            success=False,
            repository=repository_url,
            pull_request_id=pull_request_id,
            tests_created=0,
            tests_modified=0,
            tests_deleted=0,
            build_success=False,
        )

        try:
            # Step 1: Clone repository
            logger.info("Step 1: Cloning repository")
            self._clone_repository(repository_url)

            # Step 2: Get pull request info
            logger.info("Step 2: Fetching pull request details")
            self._fetch_pull_request(pull_request_id)

            # Step 3: Parse and index codebase
            logger.info("Step 3: Parsing codebase")
            self._parse_codebase()

            # Step 4: Analyze changes
            logger.info("Step 4: Analyzing changes")
            change_analysis = self._analyze_changes()

            if not change_analysis.files_needing_tests:
                logger.info("No source files need test generation")
                result.success = True
                return result

            # Step 5: Generate/update tests
            logger.info("Step 5: Generating tests")
            generation_results = self._generate_tests(change_analysis)

            result.tests_created = len([r for r in generation_results if r.action == "created"])
            result.tests_modified = len([r for r in generation_results if r.action == "updated"])

            # Step 6: Handle deleted files
            logger.info("Step 6: Processing deleted files")
            deleted_count = self._handle_deletions(change_analysis)
            result.tests_deleted = deleted_count

            # Step 7: Build and fix loop
            logger.info("Step 7: Building and fixing")
            build_result = self._build_and_fix()
            result.build_success = build_result.success

            if not result.build_success:
                result.errors.append("Build failed after fix attempts")

            # Step 8: Run tests
            logger.info("Step 8: Running tests")
            test_result = self._run_tests()
            result.test_summary = test_result

            # Step 9: Commit and push
            logger.info("Step 9: Committing changes")
            commit_sha = self._commit_changes(result)
            result.commit_sha = commit_sha

            # Step 10: Post PR comment
            logger.info("Step 10: Posting PR comment")
            self._post_pr_comment(result)

            result.success = result.build_success
            logger.info(f"Workflow completed: success={result.success}")

        except Exception as e:
            logger.error(f"Workflow failed: {e}")
            result.errors.append(str(e))

        finally:
            self._cleanup()

        return result

    def _clone_repository(self, repository_url: str) -> None:
        """Clone the repository."""
        client = self._init_azure_client()

        repo_manager = RepositoryManager(
            client=client,
            work_directory=self.settings.workflow.work_directory,
            personal_access_token=self.settings.azure_devops.personal_access_token.get_secret_value(),
        )

        self.repo_info = repo_manager.get_repository_info(repository_url)
        self.repo_path = repo_manager.clone_repository(
            self.repo_info,
            force_fresh=self.settings.workflow.force_fresh_clone,
        )
        self.git_ops = repo_manager.get_git_operations()

    def _fetch_pull_request(self, pull_request_id: int) -> None:
        """Fetch pull request details."""
        client = self._init_azure_client()
        pr_manager = PullRequestManager(client)

        self.pr_info = pr_manager.get_pull_request(self.repo_info, pull_request_id)

        # Checkout PR branch
        repo_manager = RepositoryManager(
            client=client,
            work_directory=self.settings.workflow.work_directory,
            personal_access_token=self.settings.azure_devops.personal_access_token.get_secret_value(),
        )
        repo_manager.git_ops = self.git_ops
        repo_manager.checkout_pr_branch(self.repo_info, self.pr_info.source_branch)

    def _parse_codebase(self) -> None:
        """Parse and index the codebase."""
        # Generate file tree
        tree_gen = FileTreeGenerator()
        file_tree = tree_gen.generate_tree(self.repo_path)
        tree_gen.save_tree(
            file_tree,
            self.repo_path / ".testgen" / "file_tree.json",
        )

        # Parse C# files
        parser = CSharpParser()
        parse_results = parser.parse_directory(
            self.repo_path,
            output_file=self.repo_path / ".testgen" / "csharp_index.json",
        )

        # Create searchable index
        index = parser.get_searchable_index(parse_results)
        JsonHandler.dump_file(
            index,
            self.repo_path / ".testgen" / "search_index.json",
        )

        logger.info(f"Indexed {len(parse_results)} C# files")

    def _analyze_changes(self) -> ChangeAnalysis:
        """Analyze PR changes."""
        detector = ChangeDetector(self.repo_path, self.git_ops)
        return detector.analyze_pull_request(
            self.pr_info,
            target_branch=self.pr_info.target_branch_name,
        )

    def _generate_tests(
        self,
        change_analysis: ChangeAnalysis,
    ) -> list[TestGenerationResult]:
        """Generate tests for changed files."""
        client = self._init_ollama_client()

        orchestrator = TestGeneratorOrchestrator(
            client=client,
            repo_path=self.repo_path,
        )

        return orchestrator.generate_tests_for_changes(
            change_analysis.files_needing_tests
        )

    def _handle_deletions(self, change_analysis: ChangeAnalysis) -> int:
        """Handle deleted source files by removing corresponding tests."""
        deleted_count = 0

        for context in change_analysis.files_with_deleted_tests:
            if context.test_file_path:
                test_file = self.repo_path / context.test_file_path
                if test_file.exists():
                    test_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted test file: {context.test_file_path}")

        return deleted_count

    def _build_and_fix(self) -> Any:
        """Build solution and fix any errors."""
        client = self._init_ollama_client()

        # First try a simple build
        builder = SolutionBuilder(self.repo_path)

        # Restore packages
        builder.restore()

        # Initial build
        build_result = builder.build()

        if build_result.success:
            return build_result

        # Use build fixer
        fixer = BuildFixOrchestrator(
            client=client,
            repo_path=self.repo_path,
            max_iterations=self.settings.workflow.max_build_fix_iterations,
        )

        errors = [
            {
                "file": e.file,
                "line": e.line,
                "column": e.column,
                "code": e.code,
                "message": e.message,
            }
            for e in build_result.errors
        ]

        return fixer.fix_build(initial_errors=errors)

    def _run_tests(self) -> dict:
        """Run tests and return summary."""
        runner = TestRunner(self.repo_path)
        result = runner.run_tests(no_build=True)
        return runner.get_summary(result)

    def _commit_changes(self, result: WorkflowResult) -> str | None:
        """Commit generated tests to the PR branch."""
        if not self.git_ops:
            return None

        # Check for changes
        status = self.git_ops.status()
        has_changes = (
            status["modified"] or
            status["staged"] or
            status["untracked"]
        )

        if not has_changes:
            logger.info("No changes to commit")
            return None

        # Stage all changes in tests directory
        self.git_ops.add_all()

        # Create commit message
        message = self._create_commit_message(result)

        commit_sha = self.git_ops.commit(message)
        logger.info(f"Created commit: {commit_sha[:8]}")

        # Push changes
        self.git_ops.push()

        return commit_sha

    def _create_commit_message(self, result: WorkflowResult) -> str:
        """Create commit message for generated tests."""
        lines = [
            "chore(tests): AI-generated test updates",
            "",
            f"Tests created: {result.tests_created}",
            f"Tests modified: {result.tests_modified}",
            f"Tests deleted: {result.tests_deleted}",
            "",
            "Generated by AI Test Generator (Qwen Coder 3)",
        ]
        return "\n".join(lines)

    def _post_pr_comment(self, result: WorkflowResult) -> None:
        """Post summary comment to the PR."""
        client = self._init_azure_client()
        pr_manager = PullRequestManager(client)

        comment = pr_manager.create_test_summary_comment(
            tests_added=result.tests_created,
            tests_modified=result.tests_modified,
            tests_deleted=result.tests_deleted,
            test_results=result.test_summary,
        )

        pr_manager.post_comment(
            self.repo_info,
            self.pr_info.id,
            comment,
        )

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self.ado_client:
            self.ado_client.close()
        if self.ollama_client:
            self.ollama_client.close()


def run_workflow(
    repository_url: str,
    pull_request_id: int,
    settings: Settings | None = None,
) -> WorkflowResult:
    """
    Convenience function to run the workflow.

    Args:
        repository_url: Azure DevOps repository URL
        pull_request_id: Pull request ID
        settings: Optional settings override

    Returns:
        WorkflowResult
    """
    workflow = TestGenerationWorkflow(settings)
    return workflow.run(repository_url, pull_request_id)
