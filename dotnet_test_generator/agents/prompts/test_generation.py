"""Comprehensive prompts for test generation."""

from datetime import datetime


class TestGenerationPrompts:
    """
    Comprehensive prompts for the test generation agent.

    These prompts embody best practices for DDD testing, xUnit patterns,
    and behavior-driven test design.
    """

    @staticmethod
    def get_system_prompt() -> str:
        """Get the system prompt with current date."""
        current_date = datetime.now().strftime("%Y-%m-%d")
        return """You are an expert .NET test engineer specializing in xUnit testing for Domain-Driven Design (DDD) applications. Your role is to generate high-quality, comprehensive unit tests that thoroughly validate the behavior of C# code.

**Current Date: """ + current_date + """**

## Your Core Responsibilities

1. **Analyze Source Code**: Understand the purpose, behavior, and edge cases of the code under test
2. **Generate Meaningful Tests**: Write tests that verify actual behavior, not just code coverage
3. **Follow Best Practices**: Apply DDD testing patterns and xUnit conventions
4. **Ensure Completeness**: Cover happy paths, edge cases, error conditions, and boundary values

## Testing Philosophy

### Behavior-Driven Testing
- Tests should describe WHAT the code does, not HOW it does it
- Test names should read like specifications: `MethodName_StateUnderTest_ExpectedBehavior`
- Each test should verify ONE specific behavior

### Test Structure (Arrange-Act-Assert)
```csharp
[Fact]
public void MethodName_WhenCondition_ShouldExpectedResult()
{
    // Arrange - Set up the test scenario
    var sut = new SystemUnderTest();
    var input = CreateValidInput();

    // Act - Execute the behavior being tested
    var result = sut.MethodUnderTest(input);

    // Assert - Verify the expected outcome
    Assert.NotNull(result);
    Assert.Equal(expectedValue, result.Property);
}
```

## DDD Testing Patterns

### Domain Entities
- Test invariant enforcement (constructor validation)
- Test state transitions
- Test business rule enforcement
- Verify domain events are raised when appropriate

### Value Objects
- Test equality by value
- Test immutability
- Test validation rules
- Test factory methods

### Domain Services
- Test business logic orchestration
- Mock repositories and external dependencies
- Verify correct coordination between aggregates

### Application Services / Use Cases
- Test command/query handling
- Verify proper orchestration of domain objects
- Test authorization and validation
- Mock infrastructure concerns

### Repositories (Integration Tests patterns)
- Test CRUD operations
- Test query methods
- Verify proper entity mapping

## xUnit Best Practices

### Fact vs Theory
```csharp
// Use [Fact] for single test cases
[Fact]
public void Add_TwoPositiveNumbers_ReturnsSum() { }

// Use [Theory] for parameterized tests
[Theory]
[InlineData(1, 2, 3)]
[InlineData(0, 0, 0)]
[InlineData(-1, 1, 0)]
public void Add_VariousNumbers_ReturnsCorrectSum(int a, int b, int expected) { }
```

### Test Data Builders
```csharp
// Use builders for complex object creation
var order = new OrderBuilder()
    .WithCustomer(customer)
    .WithItems(items)
    .WithShippingAddress(address)
    .Build();
```

### Assertions - BE SPECIFIC AND ACCURATE
- Use specific assertions: `Assert.Equal`, `Assert.Contains`, `Assert.Throws<T>`
- Avoid `Assert.True(condition)` when more specific assertions exist
- **Verify exact expected values**, not just "not null" or "not empty"
- For strings: Assert exact expected content, not just length
- For numbers: Assert the exact expected value
- For collections: Assert exact count AND specific items
- For exceptions: Assert the exception type AND message content
- Include meaningful failure messages when helpful
- Example of GOOD assertions:
  ```csharp
  Assert.Equal("John Doe", customer.Name);
  Assert.Equal(25, customer.Age);
  Assert.Contains("Expected item", collection);
  var ex = Assert.Throws<ArgumentException>(() => sut.Method(null));
  Assert.Contains("cannot be null", ex.Message);
  ```

## What Makes a Good Test

### DO:
- Test one behavior per test method
- Use descriptive test names that explain the scenario
- Test boundary conditions (null, empty, max values, etc.)
- Test error conditions and exception handling
- Keep tests independent and isolated
- Use meaningful variable names in tests
- Group related tests in nested classes or regions

### DON'T:
- Don't test implementation details
- Don't have logic in tests (no if/loops in test methods)
- Don't test framework or library code
- Don't share state between tests
- Don't make tests dependent on execution order
- Don't use magic numbers without explanation

## Code Coverage Goals

For each source file, aim to test:
1. **All public methods** - Every public method should have at least one test
2. **Constructor validation** - Test that invalid inputs are rejected
3. **Happy paths** - Test normal, expected usage
4. **Edge cases** - Test boundaries, empty collections, null values
5. **Error paths** - Test that errors are handled correctly
6. **State changes** - Verify object state after operations

## Output Format

When generating tests, provide:
1. Complete, compilable test file
2. **ALL necessary using statements** - this is CRITICAL:
   - `using Xunit;` - always required
   - `using System;` - for basic types
   - **`using <SourceNamespace>;`** - MUST include the namespace of the class being tested
   - Example: If testing `SampleApp.Domain.Entities.Customer`, add: `using SampleApp.Domain.Entities;`
   - Add any other namespaces needed for types used in the source file
3. Proper namespace matching test project conventions (e.g., `SampleApp.Domain.Tests.Entities`)
4. Well-organized test classes
5. No placeholder comments or TODO markers - write complete implementations

**IMPORTANT**: The test file MUST compile. Missing using statements will cause build failures.

## Tools Available

You have access to tools for:
- Reading files to understand context
- Writing test files
- Listing directories to find related code
- Running builds to verify compilation
- Running tests to verify they pass

Always use the write_file tool to create or update test files."""

    @staticmethod
    def get_test_update_prompt(
        source_file_old: str | None,
        source_file_new: str,
        test_file_current: str | None,
        source_path: str,
        test_path: str,
    ) -> str:
        """
        Generate prompt for updating tests when source file changes.

        Args:
            source_file_old: Previous version of source file (None if new file)
            source_file_new: Current version of source file
            test_file_current: Current test file content (None if doesn't exist)
            source_path: Path to source file
            test_path: Path to test file

        Returns:
            User message for the agent
        """
        prompt_parts = [
            f"## Task: Generate/Update Tests for `{source_path}`",
            "",
            "I need you to analyze the source code changes and generate comprehensive xUnit tests.",
            "",
            "### Source File Path",
            f"`{source_path}`",
            "",
        ]

        if source_file_old:
            prompt_parts.extend([
                "### Previous Version of Source File",
                "```csharp",
                source_file_old,
                "```",
                "",
                "### Current Version of Source File",
                "```csharp",
                source_file_new,
                "```",
                "",
                "### What Changed",
                "Analyze the differences between the old and new versions to understand:",
                "- New methods or properties that need tests",
                "- Changed behavior that requires test updates",
                "- Removed functionality where tests should be deleted",
                "",
            ])
        else:
            prompt_parts.extend([
                "### Source File (New File)",
                "```csharp",
                source_file_new,
                "```",
                "",
                "This is a new file that needs comprehensive tests.",
                "",
            ])

        prompt_parts.extend([
            f"### Test File Path",
            f"`{test_path}`",
            "",
        ])

        if test_file_current:
            prompt_parts.extend([
                "### Current Test File",
                "```csharp",
                test_file_current,
                "```",
                "",
                "### Instructions",
                "1. Analyze the changes between old and new source files",
                "2. Update the test file to:",
                "   - Add tests for new functionality",
                "   - Modify tests for changed behavior",
                "   - Remove tests for deleted functionality",
                "3. Ensure all tests follow best practices",
                "4. Use the write_file tool to save the updated test file",
                "",
            ])
        else:
            prompt_parts.extend([
                "### Current Test File",
                "No test file exists yet. Create a new one.",
                "",
                "### Instructions",
                "1. Analyze the source file thoroughly",
                "2. Create a comprehensive test file that covers:",
                "   - All public methods",
                "   - Constructor validation",
                "   - Happy paths for each method",
                "   - Edge cases (null inputs, empty collections, boundary values)",
                "   - Error conditions and exception handling",
                "3. Use proper test naming: `MethodName_StateUnderTest_ExpectedBehavior`",
                "4. Use the write_file tool to create the test file",
                "",
            ])

        prompt_parts.extend([
            "### Requirements",
            "- Write COMPLETE test implementations, not skeletons",
            "- Include all necessary using statements",
            "- Use xUnit attributes ([Fact], [Theory], [InlineData])",
            "- Follow Arrange-Act-Assert pattern",
            "- Prefer many small focused tests over few large tests",
            "- Test both success and failure scenarios",
            "",
            "Begin by analyzing the source code, then generate the tests.",
        ])

        return "\n".join(prompt_parts)

    @staticmethod
    def get_new_file_prompt(
        source_content: str,
        source_path: str,
        test_path: str,
        related_files: dict[str, str] | None = None,
    ) -> str:
        """
        Generate prompt for creating tests for a new source file.

        Args:
            source_content: Content of the new source file
            source_path: Path to source file
            test_path: Path to test file
            related_files: Optional dict of related file paths to content

        Returns:
            User message for the agent
        """
        prompt_parts = [
            f"## Task: Create Tests for New File `{source_path}`",
            "",
            "A new source file has been added. Generate comprehensive xUnit tests for it.",
            "",
            "### Source File",
            f"Path: `{source_path}`",
            "```csharp",
            source_content,
            "```",
            "",
        ]

        if related_files:
            prompt_parts.append("### Related Files (for context)")
            for path, content in list(related_files.items())[:3]:  # Limit context
                prompt_parts.extend([
                    f"#### `{path}`",
                    "```csharp",
                    content[:2000] + ("..." if len(content) > 2000 else ""),
                    "```",
                    "",
                ])

        prompt_parts.extend([
            f"### Test File to Create",
            f"`{test_path}`",
            "",
            "### Instructions",
            "1. Analyze the source file to understand its purpose and behavior",
            "2. Identify all testable units (public methods, constructors, properties)",
            "3. Design test cases covering:",
            "   - Normal/happy path scenarios",
            "   - Edge cases and boundary conditions",
            "   - Error handling and validation",
            "   - Any domain-specific business rules",
            "4. Write complete test implementations",
            "5. Use the write_file tool to create the test file",
            "",
            "### Test Structure Template",
            "```csharp",
            "using Xunit;",
            "using FluentAssertions; // if available",
            "using Moq; // if mocking is needed",
            "",
            "namespace YourProject.Tests;",
            "",
            "public class ClassNameTests",
            "{",
            "    // Test helper methods and fixtures here",
            "",
            "    [Fact]",
            "    public void MethodName_WhenCondition_ShouldExpectedResult()",
            "    {",
            "        // Arrange",
            "        // Act",
            "        // Assert",
            "    }",
            "}",
            "```",
            "",
            "Begin by analyzing the source code structure.",
        ])

        return "\n".join(prompt_parts)

    @staticmethod
    def get_continuation_prompt() -> str:
        """Get prompt to continue if agent stops prematurely."""
        return """You haven't completed the task yet. Please:

1. If you haven't written the test file yet, use the write_file tool now
2. If there are more test cases to add, continue writing them
3. Make sure all public methods have tests
4. Verify you've covered edge cases and error scenarios

Use the write_file tool to save the complete test file."""

    @staticmethod
    def get_context_request_prompt(requested_files: list[str]) -> str:
        """Generate prompt for requesting additional context."""
        files_list = "\n".join(f"- `{f}`" for f in requested_files)
        return f"""To write better tests, I need to see the following related files:

{files_list}

Please use the read_file tool to examine these files."""

    @staticmethod
    def get_test_validation_prompt(
        test_file_path: str,
        build_errors: list[dict] | None = None,
        test_failures: list[dict] | None = None,
    ) -> str:
        """Generate prompt for validating and fixing tests."""
        prompt_parts = [
            f"## Task: Fix Issues in Test File `{test_file_path}`",
            "",
        ]

        if build_errors:
            prompt_parts.extend([
                "### Build Errors",
                "The test file has compilation errors that need to be fixed:",
                "",
            ])
            for error in build_errors[:10]:
                prompt_parts.append(
                    f"- **Line {error.get('line', '?')}**: {error.get('code', '')} - {error.get('message', '')}"
                )
            prompt_parts.append("")

        if test_failures:
            prompt_parts.extend([
                "### Test Failures",
                "Some tests are failing:",
                "",
            ])
            for failure in test_failures[:10]:
                prompt_parts.append(f"- **{failure.get('name', 'Unknown')}**")
                if failure.get('error'):
                    prompt_parts.append(f"  Error: {failure['error'][:200]}")
            prompt_parts.append("")

        prompt_parts.extend([
            "### Instructions",
            "1. Read the current test file using read_file",
            "2. Analyze the errors and understand the root cause",
            "3. Fix the issues while maintaining test coverage",
            "4. Use write_file to save the corrected test file",
            "",
            "Fix these issues now.",
        ])

        return "\n".join(prompt_parts)
