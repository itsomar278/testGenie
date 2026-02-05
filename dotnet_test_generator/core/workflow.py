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
    tests_created: int  # Number of test FILES created
    tests_modified: int  # Number of test FILES modified
    tests_deleted: int  # Number of test FILES deleted
    build_success: bool
    test_summary: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    commit_sha: str | None = None
    total_test_methods: int = 0  # Total [Fact] and [Theory] methods written


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
        logger.info("=" * 60)
        logger.info("STARTING TEST GENERATION WORKFLOW")
        logger.info("=" * 60)
        logger.info(f"Repository URL: {repository_url}")
        logger.info(f"Pull Request ID: #{pull_request_id}")
        logger.info(f"Work Directory: {self.settings.workflow.work_directory}")
        logger.info(f"Ollama Model: {self.settings.ollama.model}")
        logger.info(f"Ollama URL: {self.settings.ollama.base_url}")

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
            logger.info("-" * 40)
            logger.info("STEP 1/10: Cloning repository")
            logger.info("-" * 40)
            self._clone_repository(repository_url)
            logger.info(f"[STEP 1 COMPLETE] Repository cloned to: {self.repo_path}")

            # Step 2: Get pull request info
            logger.info("-" * 40)
            logger.info("STEP 2/10: Fetching pull request details")
            logger.info("-" * 40)
            self._fetch_pull_request(pull_request_id)
            logger.info(f"[STEP 2 COMPLETE] PR Title: {self.pr_info.title}")
            logger.info(f"[STEP 2 COMPLETE] Source Branch: {self.pr_info.source_branch}")
            logger.info(f"[STEP 2 COMPLETE] Target Branch: {self.pr_info.target_branch_name}")
            logger.info(f"[STEP 2 COMPLETE] Changed Files: {len(self.pr_info.changes)}")

            # Step 3: Parse and index codebase
            logger.info("-" * 40)
            logger.info("STEP 3/10: Parsing codebase")
            logger.info("-" * 40)
            self._parse_codebase()
            logger.info("[STEP 3 COMPLETE] Codebase indexed successfully")

            # Step 4: Analyze changes
            logger.info("-" * 40)
            logger.info("STEP 4/10: Analyzing changes")
            logger.info("-" * 40)
            change_analysis = self._analyze_changes()
            logger.info(f"[STEP 4 COMPLETE] Source changes: {len(change_analysis.source_changes)}")
            logger.info(f"[STEP 4 COMPLETE] Test changes: {len(change_analysis.test_changes)}")
            logger.info(f"[STEP 4 COMPLETE] Other changes: {len(change_analysis.other_changes)}")
            logger.info(f"[STEP 4 COMPLETE] Files needing tests: {len(change_analysis.files_needing_tests)}")

            if not change_analysis.files_needing_tests:
                logger.info("No source files need test generation - workflow complete")
                result.success = True
                return result

            # Log files that need tests
            for ctx in change_analysis.files_needing_tests:
                logger.info(f"  - {ctx.change.path} ({ctx.change.change_type.value})")

            # Step 5: Generate/update tests
            logger.info("-" * 40)
            logger.info("STEP 5/10: Generating tests")
            logger.info("-" * 40)
            generation_results = self._generate_tests(change_analysis)

            result.tests_created = len([r for r in generation_results if r.action == "created"])
            result.tests_modified = len([r for r in generation_results if r.action == "updated"])
            result.total_test_methods = sum(r.tests_written for r in generation_results)
            logger.info(f"[STEP 5 COMPLETE] Test files created: {result.tests_created}")
            logger.info(f"[STEP 5 COMPLETE] Test files modified: {result.tests_modified}")
            logger.info(f"[STEP 5 COMPLETE] Total test methods written: {result.total_test_methods}")

            # Step 6: Handle deleted files
            logger.info("-" * 40)
            logger.info("STEP 6/10: Processing deleted files")
            logger.info("-" * 40)
            deleted_count = self._handle_deletions(change_analysis)
            result.tests_deleted = deleted_count
            logger.info(f"[STEP 6 COMPLETE] Tests deleted: {deleted_count}")

            # Step 7: Build and fix loop
            logger.info("-" * 40)
            logger.info("STEP 7/10: Building and fixing")
            logger.info("-" * 40)
            build_result = self._build_and_fix()
            result.build_success = build_result.success
            logger.info(f"[STEP 7 COMPLETE] Build success: {result.build_success}")

            if not result.build_success:
                logger.error(f"[STEP 7 FAILED] Build errors: {build_result.error_count}")
                for err in build_result.errors[:5]:
                    logger.error(f"  - {err.file}:{err.line} {err.code}: {err.message}")
                result.errors.append("Build failed after fix attempts")

            # Step 8: Run tests
            logger.info("-" * 40)
            logger.info("STEP 8/10: Running tests")
            logger.info("-" * 40)
            test_result = self._run_tests()
            result.test_summary = test_result
            logger.info(f"[STEP 8 COMPLETE] Test results: {test_result}")

            # Step 9: Commit and push
            logger.info("-" * 40)
            logger.info("STEP 9/10: Committing changes")
            logger.info("-" * 40)
            commit_sha = self._commit_changes(result)
            result.commit_sha = commit_sha
            logger.info(f"[STEP 9 COMPLETE] Commit SHA: {commit_sha or 'No changes to commit'}")

            # Step 10: Post PR comment
            logger.info("-" * 40)
            logger.info("STEP 10/10: Posting PR comment")
            logger.info("-" * 40)
            self._post_pr_comment(result)
            logger.info("[STEP 10 COMPLETE] PR comment posted")

            result.success = result.build_success
            logger.info("=" * 60)
            logger.info(f"WORKFLOW COMPLETED: {'SUCCESS' if result.success else 'FAILED'}")
            logger.info("=" * 60)

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

        logger.info("[TEST] Starting test execution...")
        result = runner.run_tests(no_build=True)

        # Log test output for debugging (show more at INFO level)
        if result.output:
            # Show last 1000 chars which typically contains the summary
            output_tail = result.output[-1500:] if len(result.output) > 1500 else result.output
            logger.info(f"[TEST] Test output (last part):\n{output_tail}")

        logger.info(f"[TEST] Test execution completed: success={result.success}")
        logger.info(f"[TEST] Results: Total={result.total}, Passed={result.passed}, Failed={result.failed}, Skipped={result.skipped}")

        if result.total == 0:
            logger.warning("[TEST] No tests discovered. Check if:")
            logger.warning("  - Test files have correct [Fact]/[Theory] attributes")
            logger.warning("  - Test project references xunit and xunit.runner.visualstudio")
            logger.warning("  - Using statements are correct")
            logger.warning("  - Build succeeded in Step 7")

        summary = runner.get_summary(result)
        logger.info(f"[TEST] Summary to be posted: {summary}")
        return summary

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

        # Only stage test files, exclude build artifacts
        self._stage_test_files_only()

        # Create commit message
        message = self._create_commit_message(result)

        commit_sha = self.git_ops.commit(message)
        logger.info(f"Created commit: {commit_sha[:8]}")

        # Push changes
        self.git_ops.push()

        return commit_sha

    def _stage_test_files_only(self) -> None:
        """Stage only test files, excluding build artifacts and other junk."""
        import subprocess

        # Patterns to exclude from staging
        exclude_patterns = [
            "bin/",
            "obj/",
            "debug/",
            "release/",
            ".vs/",
            ".idea/",
            "*.user",
            "*.suo",
            "*.cache",
            "packages/",
            "node_modules/",
            "TestResults/",
            ".nuget/",
            "*.nupkg",
            "*.snupkg",
            ".testgen/",
        ]

        # Get all untracked and modified files
        status = self.git_ops.status()
        all_files = status["modified"] + status["untracked"]

        # Filter to only test files (.cs files in tests/ directory)
        test_files = []
        for f in all_files:
            f_lower = f.lower().replace("\\", "/")

            # Must be in tests directory and be a .cs file
            if not (f_lower.startswith("tests/") or "/tests/" in f_lower):
                continue
            if not f_lower.endswith(".cs"):
                continue

            # Check against exclude patterns
            excluded = False
            for pattern in exclude_patterns:
                if pattern.rstrip("/") in f_lower:
                    excluded = True
                    break

            if not excluded:
                test_files.append(f)

        if test_files:
            logger.info(f"Staging {len(test_files)} test files")
            for f in test_files:
                logger.debug(f"  Staging: {f}")
            self.git_ops.add(test_files)
        else:
            logger.info("No test files to stage")

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
            test_files_created=result.tests_created,
            test_files_modified=result.tests_modified,
            total_test_methods=result.total_test_methods,
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
