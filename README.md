# .NET Test Generator

An AI-driven system that automatically generates, updates, and maintains xUnit tests for .NET 9 pull requests in Azure DevOps repositories.

## Overview

This tool uses local AI (Ollama with Qwen Coder 3) to analyze code changes in pull requests and generate comprehensive unit tests following Domain-Driven Design (DDD) best practices.

### Key Features

- **Automatic Test Generation**: Creates xUnit tests for new source files
- **Test Updates**: Modifies existing tests when source code changes
- **Test Cleanup**: Removes tests for deleted source files
- **Build Fix Loop**: Automatically fixes compilation errors in generated tests
- **PR Integration**: Commits changes and posts summaries to pull requests
- **Semantic Code Analysis**: Uses tree-sitter for C# parsing

## Prerequisites

### Required Software

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.11+ | Runtime |
| .NET SDK | 9.0 | Build and test execution |
| Ollama | Latest | Local AI inference |
| Git | 2.x | Repository operations |

### Ollama Setup

1. Install Ollama from [ollama.ai](https://ollama.ai)

2. Pull the Qwen Coder model:
   ```bash
   ollama pull qwen2.5-coder:32b
   ```

3. Verify Ollama is running:
   ```bash
   ollama list
   ```

### Azure DevOps Setup

1. Create a Personal Access Token (PAT) with these scopes:
   - **Code**: Read & Write
   - **Pull Request Threads**: Read & Write

2. Note your organization URL: `https://dev.azure.com/YOUR_ORG`

## Installation

```bash
# Clone or navigate to the project
cd PythonProjectss

# Install dependencies with uv
uv sync

# Or with pip
pip install -e .
```

## Configuration

### Option 1: Environment Variables

Create a `.env` file in the project root:

```env
# Azure DevOps (Required)
AZURE_DEVOPS_ORGANIZATION_URL=https://dev.azure.com/your-organization
AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN=your-pat-token-here
AZURE_DEVOPS_PROJECT=your-project-name

# Ollama (Optional - defaults shown)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:32b
OLLAMA_NUM_CTX=32768
OLLAMA_TEMPERATURE=0.1
OLLAMA_TIMEOUT=600

# Workflow (Optional - defaults shown)
WORKFLOW_WORK_DIRECTORY=./workdir
WORKFLOW_MAX_BUILD_FIX_ITERATIONS=10
WORKFLOW_FORCE_FRESH_CLONE=true

# Logging (Optional)
LOG_LEVEL=INFO
LOG_RICH_CONSOLE=true
```

### Option 2: Command Line Arguments

All settings can be passed as CLI arguments (see Usage below).

## Usage

### Generate Tests for a Pull Request

```bash
uv run testgen generate <REPOSITORY_URL> <PR_ID> \
  --pat <PAT_TOKEN> \
  --organization <ORG_URL> \
  --project <PROJECT_NAME>
```

**Example:**
```bash
uv run testgen generate \
  "https://dev.azure.com/myorg/myproject/_git/myrepo" \
  123 \
  --pat "xxxxxxxxxxxxxxxxxxxx" \
  --organization "https://dev.azure.com/myorg" \
  --project "myproject" \
  --model "qwen2.5-coder:32b" \
  --verbose
```

### Check Ollama Connection

```bash
uv run testgen check
```

### Index a Repository (for debugging)

```bash
uv run testgen index /path/to/repo -o index.json
```

### Analyze Solution Structure

```bash
uv run testgen analyze /path/to/repo
```

## How It Works

### Workflow Steps

1. **Clone Repository**: Fetches the repository and checks out the PR branch
2. **Parse Codebase**: Uses tree-sitter to create a semantic index of C# code
3. **Detect Changes**: Identifies modified, added, and deleted files in the PR
4. **Generate Tests**: For each changed source file:
   - **Modified**: Updates existing tests to match new behavior
   - **Added**: Creates new test file with comprehensive coverage
   - **Deleted**: Removes corresponding test file
5. **Build & Fix**: Compiles the solution; if errors occur, the AI fixes them iteratively
6. **Run Tests**: Executes all tests and collects results
7. **Commit & Push**: Commits generated tests to the PR branch
8. **Post Comment**: Adds a summary comment to the pull request

### Repository Structure Expectations

The tool expects a standard .NET solution structure:

```
/
├── src/
│   ├── MyProject.Api/
│   │   └── Controllers/
│   ├── MyProject.Domain/
│   │   └── Entities/
│   ├── MyProject.Application/
│   │   └── Services/
│   └── MyProject.Infrastructure/
│       └── Repositories/
├── tests/
│   ├── MyProject.Api.Tests/
│   ├── MyProject.Domain.Tests/
│   ├── MyProject.Application.Tests/
│   └── MyProject.Infrastructure.Tests/
└── MySolution.sln
```

### Test File Mapping

Source files are mapped to test files by convention:

| Source Path | Test Path |
|-------------|-----------|
| `src/MyProject/Services/UserService.cs` | `tests/MyProject.Tests/Services/UserServiceTests.cs` |
| `src/MyProject.Domain/Entities/Order.cs` | `tests/MyProject.Domain.Tests/Entities/OrderTests.cs` |

## Test Generation Quality

The AI generates tests following these principles:

### DDD Testing Patterns
- Domain entities: Invariant enforcement, state transitions, business rules
- Value objects: Equality, immutability, validation
- Domain services: Orchestration, aggregate coordination
- Application services: Command/query handling, authorization

### xUnit Best Practices
- Arrange-Act-Assert pattern
- Descriptive naming: `MethodName_StateUnderTest_ExpectedBehavior`
- `[Fact]` for single cases, `[Theory]` with `[InlineData]` for parameterized tests
- One assertion concept per test

### Coverage Goals
- All public methods
- Constructor validation
- Happy paths
- Edge cases (null, empty, boundary values)
- Error conditions and exceptions

## CLI Reference

```
testgen --help
testgen generate --help
testgen check --help
testgen index --help
testgen analyze --help
```

### Generate Command Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `--pat` | `AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN` | Required | Azure DevOps PAT |
| `--organization` | `AZURE_DEVOPS_ORGANIZATION_URL` | Required | Organization URL |
| `--project` | `AZURE_DEVOPS_PROJECT` | Required | Project name |
| `--ollama-url` | `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `--model` | `OLLAMA_MODEL` | `qwen2.5-coder:32b` | Model to use |
| `--work-dir` | `WORKFLOW_WORK_DIRECTORY` | `./workdir` | Clone directory |
| `--max-iterations` | `WORKFLOW_MAX_BUILD_FIX_ITERATIONS` | `10` | Max build fix attempts |
| `-v, --verbose` | `LOG_LEVEL=DEBUG` | `False` | Enable debug logging |

## Project Architecture

```
dotnet_test_generator/
├── cli.py                 # Command-line interface
├── config.py              # Settings management
├── core/
│   ├── exceptions.py      # Custom exceptions
│   └── workflow.py        # Main orchestrator
├── azure_devops/
│   ├── client.py          # REST API client
│   ├── repository.py      # Clone operations
│   └── pull_request.py    # PR management
├── git/
│   └── operations.py      # Git commands
├── parsing/
│   ├── csharp_parser.py   # Tree-sitter C# parsing
│   ├── file_tree.py       # Directory indexing
│   └── change_detector.py # PR change analysis
├── agents/
│   ├── ollama_client.py   # Ollama API client
│   ├── base.py            # Base agent class
│   ├── test_generator.py  # Test generation agent
│   ├── build_fixer.py     # Build error fixer
│   ├── tools/             # Agent tools
│   │   ├── file_tools.py  # Read/write/delete
│   │   ├── git_tools.py   # Diff/status
│   │   └── dotnet_tools.py# Build/test
│   └── prompts/
│       ├── test_generation.py  # DDD testing prompts
│       └── build_fixing.py     # Error fix prompts
├── dotnet/
│   ├── solution.py        # Solution analysis
│   ├── builder.py         # Build operations
│   └── test_runner.py     # Test execution
└── utils/
    ├── logging.py         # Rich logging
    └── json_utils.py      # JSON utilities
```

## Troubleshooting

### Ollama Connection Failed

```
[FAIL] Ollama server is not available
```

**Solutions:**
- Ensure Ollama is running: `ollama serve`
- Check the URL: default is `http://localhost:11434`
- Verify the model is pulled: `ollama list`

### Azure DevOps Authentication Failed

```
AzureDevOpsError: API request failed: 401
```

**Solutions:**
- Verify PAT has not expired
- Check PAT has required scopes (Code: Read & Write)
- Ensure organization URL is correct

### Build Errors Not Fixed

If the AI cannot fix build errors after max iterations:
- Check the generated test files manually
- Ensure the source project compiles independently
- Review the build error messages in the output

### Out of Memory

For large repositories:
- Reduce `OLLAMA_NUM_CTX` to use less context
- Use a smaller model variant
- Process fewer files per run

## Limitations

- Only supports C# (.cs) files
- Requires xUnit test framework
- Test projects must follow naming convention (`*.Tests`)
- Single solution per repository recommended

## License

MIT License