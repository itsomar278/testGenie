"""Prompts for the build fixing agent."""


class BuildFixingPrompts:
    """
    Prompts for the build fixer agent.

    Guides the agent through iterative build error resolution.
    """

    SYSTEM_PROMPT = """You are a .NET build error specialist. Your role is to analyze build errors and fix them systematically.

## Your Responsibilities

1. **Analyze Build Errors**: Understand the root cause of each error
2. **Fix Issues**: Make minimal, targeted changes to fix compilation errors
3. **Preserve Functionality**: Don't change behavior, only fix compilation issues
4. **Work Iteratively**: Fix one error at a time, verify with builds

## Common Build Error Categories

### Missing References
- CS0246: Type or namespace not found
- CS0234: Namespace doesn't exist
- **Fix**: Add using statements or project references

### Type Mismatches
- CS0029: Cannot convert type
- CS1503: Argument type mismatch
- **Fix**: Correct the type or add proper conversion

### Member Access Errors
- CS1061: Type doesn't contain member
- CS0117: Type doesn't contain definition
- **Fix**: Use correct member name or add missing implementation

### Syntax Errors
- CS1002: Missing semicolon
- CS1513: Expected closing brace
- **Fix**: Correct syntax issues

### Signature Mismatches
- CS0115: No suitable method to override
- CS0534: Doesn't implement interface member
- **Fix**: Correct method signature

## Fixing Strategy

1. **Read the error message carefully**
   - Note the file, line number, and error code
   - Understand what the compiler expected vs what it found

2. **Examine the source code**
   - Read the file containing the error
   - Look at surrounding context

3. **Make minimal changes**
   - Fix only what's necessary for compilation
   - Don't refactor or improve code
   - Don't add new functionality

4. **Verify the fix**
   - Save the file
   - Run build again to confirm fix worked
   - Check for new errors introduced

## Important Guidelines

- **Focus on compilation, not logic**: Your job is to make code compile, not to fix business logic
- **Preserve test intent**: If fixing tests, keep the original test purpose
- **Don't delete tests**: Unless they test removed functionality
- **Be conservative**: Minimal changes reduce risk of breaking things

## Tools Available

- read_file: Read source files to understand context
- write_file: Save fixed files
- dotnet_build: Verify fixes compile
- list_directory: Find related files if needed
- search_files: Search for definitions or usages

Always verify your fix with dotnet_build after making changes."""

    @staticmethod
    def get_build_error_prompt(
        errors: list[dict],
        iteration: int,
        max_iterations: int,
    ) -> str:
        """
        Generate prompt for fixing build errors.

        Args:
            errors: List of build error dictionaries
            iteration: Current iteration number
            max_iterations: Maximum iterations allowed

        Returns:
            User message for the agent
        """
        prompt_parts = [
            f"## Build Error Fix - Iteration {iteration}/{max_iterations}",
            "",
            "The project failed to build. Please fix the following errors:",
            "",
            "### Build Errors",
        ]

        # Group errors by file
        errors_by_file: dict[str, list[dict]] = {}
        for error in errors:
            file_path = error.get("file", "Unknown")
            if file_path not in errors_by_file:
                errors_by_file[file_path] = []
            errors_by_file[file_path].append(error)

        for file_path, file_errors in errors_by_file.items():
            prompt_parts.append(f"\n#### `{file_path}`")
            for err in file_errors:
                line = err.get("line", "?")
                code = err.get("code", "")
                message = err.get("message", "Unknown error")
                prompt_parts.append(f"- Line {line}: **{code}** - {message}")

        prompt_parts.extend([
            "",
            "### Instructions",
            "1. Start by reading the file(s) with errors using read_file",
            "2. Analyze each error and understand the root cause",
            "3. Make the minimal fix required",
            "4. Save the fixed file using write_file",
            "5. Run dotnet_build to verify the fix",
            "",
            "**Important**: Fix one file at a time. Start with the first file listed.",
            "",
            "Begin by reading the first file that has errors.",
        ])

        return "\n".join(prompt_parts)

    @staticmethod
    def get_single_error_prompt(error: dict) -> str:
        """
        Generate prompt for fixing a single build error.

        Args:
            error: Build error dictionary

        Returns:
            User message for the agent
        """
        file_path = error.get("file", "Unknown")
        line = error.get("line", "?")
        column = error.get("column", "?")
        code = error.get("code", "")
        message = error.get("message", "Unknown error")

        return f"""## Fix Build Error

### Error Details
- **File**: `{file_path}`
- **Location**: Line {line}, Column {column}
- **Code**: {code}
- **Message**: {message}

### Instructions
1. Read the file using read_file
2. Look at line {line} and surrounding context
3. Understand why the error occurred
4. Make the minimal fix needed
5. Save the file using write_file
6. Verify with dotnet_build

Focus on this single error. Begin by reading the file."""

    @staticmethod
    def get_test_failure_fix_prompt(
        test_failures: list[dict],
        iteration: int,
    ) -> str:
        """
        Generate prompt for fixing test failures.

        Args:
            test_failures: List of failed test dictionaries
            iteration: Current iteration number

        Returns:
            User message for the agent
        """
        prompt_parts = [
            f"## Fix Failing Tests - Iteration {iteration}",
            "",
            "Some tests are failing. Analyze and fix them.",
            "",
            "### Failing Tests",
        ]

        for test in test_failures[:10]:
            name = test.get("name", "Unknown")
            error = test.get("error", "No error message")
            prompt_parts.extend([
                f"\n#### `{name}`",
                "```",
                error[:500] if error else "No error details",
                "```",
            ])

        prompt_parts.extend([
            "",
            "### Instructions",
            "1. For each failing test, determine if the issue is:",
            "   - **Test bug**: The test itself is incorrect",
            "   - **Source bug**: The production code has a bug",
            "   - **Expectation mismatch**: Test expectations need updating",
            "2. Fix the appropriate file (test or source)",
            "3. Re-run tests to verify",
            "",
            "**Important**: Only fix actual bugs. If a test is correctly identifying",
            "a source code issue, fix the source code, not the test.",
            "",
            "Begin by analyzing the first failing test.",
        ])

        return "\n".join(prompt_parts)

    @staticmethod
    def get_success_prompt() -> str:
        """Get prompt confirming successful build."""
        return """## Build Successful!

The project now compiles successfully.

Please confirm by stating: "Build fix complete. The project compiles successfully."
"""

    @staticmethod
    def get_iteration_limit_prompt(remaining: int) -> str:
        """Get prompt warning about iteration limit."""
        return f"""## Warning: Iteration Limit Approaching

You have {remaining} iterations remaining to fix the build errors.

Focus on the most critical errors first. If you cannot fix all errors,
prioritize:
1. Errors in test files (to ensure tests compile)
2. Simple syntax errors
3. Missing using statements

Continue fixing errors."""
