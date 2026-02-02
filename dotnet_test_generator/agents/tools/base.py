"""Base tool class and registry for agent tools."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from dotnet_test_generator.agents.ollama_client import ToolDefinition
from dotnet_test_generator.core.exceptions import ToolExecutionError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: str
    error: str | None = None
    data: Any = None

    def to_message(self) -> str:
        """Convert to message format for LLM."""
        if self.success:
            return self.output
        else:
            return f"Error: {self.error}\n{self.output}" if self.output else f"Error: {self.error}"


class BaseTool(ABC):
    """
    Base class for agent tools.

    Tools provide capabilities that agents can invoke during execution.
    Each tool has a name, description, and parameter schema.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name (used by LLM to call the tool)."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for the LLM."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema for tool parameters."""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """
        Execute the tool.

        Args:
            **kwargs: Tool parameters

        Returns:
            ToolResult with execution outcome
        """
        pass

    def get_definition(self) -> ToolDefinition:
        """Get tool definition for LLM."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def _validate_required_params(
        self,
        kwargs: dict,
        required: list[str],
    ) -> str | None:
        """
        Validate required parameters are present.

        Args:
            kwargs: Provided parameters
            required: Required parameter names

        Returns:
            Error message or None if valid
        """
        missing = [p for p in required if p not in kwargs or kwargs[p] is None]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        return None


@dataclass
class ToolRegistry:
    """Registry for managing available tools."""

    tools: dict[str, BaseTool] = field(default_factory=dict)

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self.tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        if name in self.tools:
            del self.tools[name]

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self.tools.get(name)

    def get_definitions(self) -> list[ToolDefinition]:
        """Get all tool definitions."""
        return [tool.get_definition() for tool in self.tools.values()]

    def execute(self, name: str, arguments: dict) -> ToolResult:
        """
        Execute a tool by name.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            ToolResult from execution
        """
        tool = self.tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown tool: {name}",
            )

        try:
            logger.debug(f"Executing tool: {name}")
            result = tool.execute(**arguments)
            return result

        except Exception as e:
            logger.error(f"Tool execution failed: {name} - {e}")
            return ToolResult(
                success=False,
                output="",
                error=str(e),
            )

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self.tools.keys())
