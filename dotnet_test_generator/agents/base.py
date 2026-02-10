"""Base agent class for AI-driven operations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from dotnet_test_generator.agents.ollama_client import (
    OllamaClient,
    Message,
    ToolDefinition,
    ChatResponse,
)
from dotnet_test_generator.agents.tools.base import BaseTool, ToolResult, ToolRegistry
from dotnet_test_generator.core.exceptions import AgentError, ContextOverflowError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AgentConfig:
    """Configuration for an agent."""

    name: str
    max_iterations: int = 50
    max_context_tokens: int = 80000
    temperature: float = 0.1
    verbose: bool = True


@dataclass
class AgentState:
    """Runtime state of an agent."""

    messages: list[Message] = field(default_factory=list)
    iteration: int = 0
    total_tokens_used: int = 0
    tool_calls_made: int = 0
    errors: list[str] = field(default_factory=list)
    completed: bool = False
    result: Any = None


class BaseAgent(ABC):
    """
    Base class for AI agents.

    Provides the core agent loop with tool calling support.
    Subclasses implement specific agent behaviors.
    """

    def __init__(
        self,
        client: OllamaClient,
        config: AgentConfig,
        tools: list[BaseTool] | None = None,
    ):
        """
        Initialize agent.

        Args:
            client: Ollama client for LLM inference
            config: Agent configuration
            tools: Available tools
        """
        self.client = client
        self.config = config
        self.registry = ToolRegistry()

        if tools:
            for tool in tools:
                self.registry.register(tool)

        self.state = AgentState()
        self._on_message_callback: Callable[[Message], None] | None = None
        self._on_tool_call_callback: Callable[[str, dict, ToolResult], None] | None = None
        self._consecutive_no_tool_calls = 0
        self._max_consecutive_no_tool_calls = 3

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """Set callback for new messages."""
        self._on_message_callback = callback

    def on_tool_call(
        self,
        callback: Callable[[str, dict, ToolResult], None],
    ) -> None:
        """Set callback for tool calls."""
        self._on_tool_call_callback = callback

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Get the system prompt for this agent."""
        pass

    @abstractmethod
    def get_initial_user_message(self, **kwargs) -> str:
        """Get the initial user message to start the conversation."""
        pass

    @abstractmethod
    def process_result(self, response: ChatResponse) -> Any:
        """Process the final response and extract result."""
        pass

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Get tool definitions for the LLM."""
        return self.registry.get_definitions()

    def run(self, **kwargs) -> Any:
        """
        Run the agent loop.

        Args:
            **kwargs: Arguments passed to get_initial_user_message

        Returns:
            Agent result (type depends on subclass)
        """
        logger.info(f"Starting agent: {self.config.name}")

        # Reset state
        self.state = AgentState()

        # Initialize conversation
        system_prompt = self.get_system_prompt()
        initial_message = self.get_initial_user_message(**kwargs)

        self.state.messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=initial_message),
        ]

        if self._on_message_callback:
            self._on_message_callback(self.state.messages[-1])

        tools = self.get_tool_definitions()

        # Agent loop
        while not self.state.completed and self.state.iteration < self.config.max_iterations:
            self.state.iteration += 1
            logger.debug(f"Agent iteration {self.state.iteration}")

            # Check context limit
            self._check_context_limit()

            # Get LLM response
            try:
                response = self.client.chat(
                    messages=self.state.messages,
                    tools=tools if tools else None,
                )
            except Exception as e:
                logger.error(f"LLM request failed: {e}")
                self.state.errors.append(str(e))
                raise AgentError(
                    f"LLM request failed: {e}",
                    agent_name=self.config.name,
                    iteration=self.state.iteration,
                ) from e

            self.state.total_tokens_used += response.total_tokens

            # Handle tool calls
            if response.has_tool_calls:
                self._consecutive_no_tool_calls = 0
                self._handle_tool_calls(response)

                # Also check if task is complete after tool calls
                # (e.g., if the write_file tool wrote the test file)
                if self._is_task_complete(response):
                    self.state.completed = True
                    self.state.result = self.process_result(response)
            else:
                # No tool calls - track consecutive misses
                self._consecutive_no_tool_calls += 1

                assistant_msg = Message(role="assistant", content=response.content)
                self.state.messages.append(assistant_msg)

                if self._on_message_callback:
                    self._on_message_callback(assistant_msg)

                # Let subclass decide if we're done
                if self._is_task_complete(response):
                    self.state.completed = True
                    self.state.result = self.process_result(response)
                elif self._consecutive_no_tool_calls >= self._max_consecutive_no_tool_calls:
                    # LLM is stuck producing text without calling tools
                    logger.warning(
                        f"Agent stuck: {self._consecutive_no_tool_calls} consecutive "
                        f"responses without tool calls. Stopping."
                    )
                    self.state.completed = True
                    self.state.result = self.process_result(response)
                else:
                    # Continue conversation
                    continuation = self._get_continuation_prompt(response)
                    if continuation:
                        self.state.messages.append(
                            Message(role="user", content=continuation)
                        )
                    else:
                        # No continuation, mark as complete
                        self.state.completed = True
                        self.state.result = self.process_result(response)

        if not self.state.completed:
            logger.warning(f"Agent hit max iterations: {self.config.max_iterations}")
            self.state.errors.append("Max iterations reached")
            # Ensure we still produce a result even on max iterations
            if self.state.result is None:
                try:
                    self.state.result = self.process_result(response)
                except Exception:
                    pass

        logger.info(
            f"Agent completed: {self.state.iteration} iterations, "
            f"{self.state.tool_calls_made} tool calls, "
            f"{self.state.total_tokens_used} tokens"
        )

        return self.state.result

    def _check_context_limit(self) -> None:
        """Check if context is within limits."""
        total_content = "".join(m.content for m in self.state.messages)
        estimated_tokens = self.client.count_tokens_estimate(total_content)

        if estimated_tokens > self.config.max_context_tokens:
            raise ContextOverflowError(
                "Context limit exceeded",
                current_tokens=estimated_tokens,
                max_tokens=self.config.max_context_tokens,
            )

    def _handle_tool_calls(self, response: ChatResponse) -> None:
        """Handle tool calls from the LLM response."""
        # Add assistant message with tool calls
        assistant_msg = Message(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )
        self.state.messages.append(assistant_msg)

        if self._on_message_callback:
            self._on_message_callback(assistant_msg)

        # Execute each tool call
        for tool_call in response.tool_calls:
            function = tool_call.get("function", {})
            tool_name = function.get("name", "")
            arguments = function.get("arguments", {})

            # Parse arguments if string
            if isinstance(arguments, str):
                try:
                    import json
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}

            logger.debug(f"Tool call: {tool_name}({arguments})")
            self.state.tool_calls_made += 1

            # Execute tool
            result = self.registry.execute(tool_name, arguments)

            if self._on_tool_call_callback:
                self._on_tool_call_callback(tool_name, arguments, result)

            # Add tool result message
            tool_msg = Message(
                role="tool",
                content=result.to_message(),
                tool_call_id=tool_call.get("id", ""),
            )
            self.state.messages.append(tool_msg)

    def _is_task_complete(self, response: ChatResponse) -> bool:
        """
        Check if the task is complete.

        Default implementation checks for completion markers.
        Subclasses can override for custom logic.
        """
        content = response.content.lower()
        completion_markers = [
            "task complete",
            "task completed",
            "all tests written",
            "tests generated",
            "finished",
            "done",
        ]
        return any(marker in content for marker in completion_markers)

    def _get_continuation_prompt(self, response: ChatResponse) -> str | None:
        """
        Get a continuation prompt if task is not complete.

        Subclasses can override for custom continuation logic.
        """
        return None

    def add_context_message(self, content: str, role: str = "user") -> None:
        """
        Add additional context to the conversation.

        Args:
            content: Message content
            role: Message role (user or system)
        """
        self.state.messages.append(Message(role=role, content=content))

    def get_conversation_history(self) -> list[dict]:
        """Get conversation history as dictionaries."""
        return [
            {"role": m.role, "content": m.content}
            for m in self.state.messages
        ]
