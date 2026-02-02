"""Configuration management for the test generator."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AzureDevOpsSettings(BaseSettings):
    """Azure DevOps connection settings."""

    model_config = SettingsConfigDict(env_prefix="AZURE_DEVOPS_")

    organization_url: str = Field(
        description="Azure DevOps organization URL (e.g., https://dev.azure.com/org)"
    )
    personal_access_token: SecretStr = Field(
        description="Personal Access Token for authentication"
    )
    project: str = Field(description="Azure DevOps project name")


class OllamaSettings(BaseSettings):
    """Ollama AI runtime settings."""

    model_config = SettingsConfigDict(env_prefix="OLLAMA_")

    base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL",
    )
    model: str = Field(
        default="qwen2.5-coder:32b",
        description="Model to use for generation",
    )
    context_window: int = Field(
        default=85000,
        description="Maximum context window size in tokens",
    )
    temperature: float = Field(
        default=0.1,
        description="Generation temperature (lower = more deterministic)",
    )
    num_ctx: int = Field(
        default=32768,
        description="Context size for the model",
    )
    timeout: int = Field(
        default=600,
        description="Request timeout in seconds",
    )


class WorkflowSettings(BaseSettings):
    """Workflow execution settings."""

    model_config = SettingsConfigDict(env_prefix="WORKFLOW_")

    work_directory: Path = Field(
        default=Path("./workdir"),
        description="Directory for cloned repositories and artifacts",
    )
    max_build_fix_iterations: int = Field(
        default=10,
        description="Maximum iterations for build fix loop",
    )
    max_test_fix_iterations: int = Field(
        default=5,
        description="Maximum iterations for test fix loop",
    )
    force_fresh_clone: bool = Field(
        default=True,
        description="Always delete and re-clone repository",
    )
    parallel_file_processing: bool = Field(
        default=False,
        description="Process changed files in parallel (experimental)",
    )


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )
    format: str = Field(
        default="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        description="Log message format",
    )
    file: Path | None = Field(
        default=None,
        description="Log file path (None for console only)",
    )
    rich_console: bool = Field(
        default=True,
        description="Use rich console for prettier output",
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    azure_devops: AzureDevOpsSettings = Field(default_factory=AzureDevOpsSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables and .env file."""
        return cls(
            azure_devops=AzureDevOpsSettings(),
            ollama=OllamaSettings(),
            workflow=WorkflowSettings(),
            logging=LoggingSettings(),
        )


# Global settings instance (lazy loaded)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def configure_settings(settings: Settings) -> None:
    """Override the global settings instance."""
    global _settings
    _settings = settings
