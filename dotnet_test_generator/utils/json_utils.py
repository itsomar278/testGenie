"""JSON handling utilities with orjson for performance."""

from pathlib import Path
from typing import Any

import orjson


class JsonHandler:
    """High-performance JSON handler using orjson."""

    @staticmethod
    def dumps(data: Any, pretty: bool = False) -> str:
        """
        Serialize data to JSON string.

        Args:
            data: Data to serialize
            pretty: Whether to format with indentation

        Returns:
            JSON string
        """
        options = orjson.OPT_SORT_KEYS
        if pretty:
            options |= orjson.OPT_INDENT_2

        return orjson.dumps(data, option=options).decode("utf-8")

    @staticmethod
    def loads(json_str: str) -> Any:
        """
        Parse JSON string to Python object.

        Args:
            json_str: JSON string to parse

        Returns:
            Parsed Python object
        """
        return orjson.loads(json_str)

    @staticmethod
    def dump_file(data: Any, path: Path, pretty: bool = True) -> None:
        """
        Write data to JSON file.

        Args:
            data: Data to write
            path: File path
            pretty: Whether to format with indentation
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        options = orjson.OPT_SORT_KEYS
        if pretty:
            options |= orjson.OPT_INDENT_2

        with open(path, "wb") as f:
            f.write(orjson.dumps(data, option=options))

    @staticmethod
    def load_file(path: Path) -> Any:
        """
        Load data from JSON file.

        Args:
            path: File path

        Returns:
            Parsed Python object
        """
        with open(path, "rb") as f:
            return orjson.loads(f.read())

    @staticmethod
    def safe_loads(json_str: str, default: Any = None) -> Any:
        """
        Safely parse JSON string, returning default on error.

        Args:
            json_str: JSON string to parse
            default: Default value if parsing fails

        Returns:
            Parsed object or default
        """
        try:
            return orjson.loads(json_str)
        except (orjson.JSONDecodeError, TypeError):
            return default

    @staticmethod
    def extract_json_from_text(text: str) -> str | None:
        """
        Extract JSON object or array from text that may contain other content.

        Useful for parsing LLM responses that include JSON within prose.

        Args:
            text: Text that may contain JSON

        Returns:
            Extracted JSON string or None
        """
        # Try to find JSON object
        start_idx = text.find("{")
        if start_idx != -1:
            brace_count = 0
            for i, char in enumerate(text[start_idx:], start_idx):
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        candidate = text[start_idx : i + 1]
                        try:
                            orjson.loads(candidate)
                            return candidate
                        except orjson.JSONDecodeError:
                            pass

        # Try to find JSON array
        start_idx = text.find("[")
        if start_idx != -1:
            bracket_count = 0
            for i, char in enumerate(text[start_idx:], start_idx):
                if char == "[":
                    bracket_count += 1
                elif char == "]":
                    bracket_count -= 1
                    if bracket_count == 0:
                        candidate = text[start_idx : i + 1]
                        try:
                            orjson.loads(candidate)
                            return candidate
                        except orjson.JSONDecodeError:
                            pass

        return None
