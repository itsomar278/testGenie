"""Ollama API client for local LLM inference."""

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from dotnet_test_generator.core.exceptions import AgentError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Message:
    """Chat message."""

    role: str  # system, user, assistant, tool
    content: str
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolDefinition:
    """Tool definition for function calling."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict:
        """Convert to Ollama tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ChatResponse:
    """Response from chat completion."""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    total_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


class OllamaClient:
    """
    Client for Ollama API.

    Handles chat completions with support for tool/function calling.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:32b",
        timeout: int = 600,
        num_ctx: int = 32768,
        temperature: float = 0.1,
    ):
        """
        Initialize Ollama client.

        Args:
            base_url: Ollama server URL
            model: Model to use
            timeout: Request timeout in seconds
            num_ctx: Context window size
            temperature: Generation temperature
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.temperature = temperature

        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def is_available(self) -> bool:
        """Check if Ollama server is available."""
        logger.debug(f"Checking Ollama availability at {self.base_url}")
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            available = response.status_code == 200
            logger.info(f"Ollama server {'available' if available else 'not available'} at {self.base_url}")
            return available
        except Exception as e:
            logger.error(f"Ollama connection failed: {e}")
            return False

    def list_models(self) -> list[str]:
        """List available models."""
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning(f"Failed to list models: {e}")
        return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        stream: bool = False,
    ) -> ChatResponse:
        """
        Send chat completion request.

        Args:
            messages: Chat messages
            tools: Available tools for function calling
            stream: Whether to stream response

        Returns:
            ChatResponse with generated content and any tool calls
        """
        payload = {
            "model": self.model,
            "messages": [self._message_to_dict(m) for m in messages],
            "stream": stream,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
            },
        }

        if tools:
            payload["tools"] = [t.to_dict() for t in tools]

        logger.info(f"[OLLAMA] Sending chat request to model: {self.model}")
        logger.info(f"[OLLAMA] Messages: {len(messages)}, Tools: {len(tools or [])}")
        logger.debug(f"[OLLAMA] Context window: {self.num_ctx}, Temperature: {self.temperature}")

        try:
            if stream:
                response = self._chat_stream(payload)
            else:
                response = self._chat_sync(payload)

            logger.info(f"[OLLAMA] Response received - Tokens: {response.total_tokens}")
            logger.info(f"[OLLAMA] Tool calls: {len(response.tool_calls)}, Finish reason: {response.finish_reason}")
            if response.content:
                logger.debug(f"[OLLAMA] Response preview: {response.content[:200]}...")
            return response

        except httpx.RequestError as e:
            logger.error(f"[OLLAMA] Request failed: {e}")
            raise AgentError(f"Ollama request failed: {e}") from e

    def _message_to_dict(self, message: Message) -> dict:
        """Convert Message to API format."""
        result = {
            "role": message.role,
            "content": message.content,
        }
        if message.tool_calls:
            result["tool_calls"] = message.tool_calls
        if message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id
        return result

    def _chat_sync(self, payload: dict) -> ChatResponse:
        """Non-streaming chat request."""
        response = self.client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )

        if response.status_code != 200:
            raise AgentError(
                f"Ollama chat failed: {response.status_code} - {response.text}"
            )

        data = response.json()
        message = data.get("message", {})

        return ChatResponse(
            content=message.get("content", ""),
            tool_calls=message.get("tool_calls", []),
            finish_reason="tool_calls" if message.get("tool_calls") else "stop",
            total_tokens=data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
        )

    def _chat_stream(self, payload: dict) -> ChatResponse:
        """Streaming chat request."""
        content_parts = []
        tool_calls = []
        total_tokens = 0

        with self.client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json=payload,
        ) as response:
            if response.status_code != 200:
                raise AgentError(f"Ollama chat failed: {response.status_code}")

            for line in response.iter_lines():
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    message = data.get("message", {})

                    if message.get("content"):
                        content_parts.append(message["content"])

                    if message.get("tool_calls"):
                        tool_calls.extend(message["tool_calls"])

                    if data.get("done"):
                        total_tokens = (
                            data.get("eval_count", 0) +
                            data.get("prompt_eval_count", 0)
                        )

                except json.JSONDecodeError:
                    continue

        return ChatResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            total_tokens=total_tokens,
        )

    def generate(
        self,
        prompt: str,
        system: str | None = None,
    ) -> str:
        """
        Simple generation without chat format.

        Args:
            prompt: Generation prompt
            system: Optional system prompt

        Returns:
            Generated text
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
            },
        }

        if system:
            payload["system"] = system

        response = self.client.post(
            f"{self.base_url}/api/generate",
            json=payload,
        )

        if response.status_code != 200:
            raise AgentError(f"Ollama generate failed: {response.status_code}")

        data = response.json()
        return data.get("response", "")

    def count_tokens_estimate(self, text: str) -> int:
        """
        Estimate token count for text.

        This is a rough estimate - actual tokenization varies by model.
        Uses ~4 characters per token as approximation.

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        return len(text) // 4
