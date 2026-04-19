import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.client import LLMClient


class _FakeAnthropicMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="json_output",
                    input={"title": "Traffic spike", "confidence": 0.8},
                )
            ]
        )


@pytest.mark.unit
def test_anthropic_complete_json_schema_uses_forced_tool_call() -> None:
    client = LLMClient.__new__(LLMClient)
    client.provider = "anthropic"
    client.model = "claude-test"
    messages = _FakeAnthropicMessages()
    client._client = SimpleNamespace(messages=messages)

    result = client.complete_json(
        "system",
        "prompt",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["title", "confidence"],
        },
    )

    assert json.loads(result) == {"title": "Traffic spike", "confidence": 0.8}
    assert messages.kwargs is not None
    assert messages.kwargs["messages"][-1]["role"] == "user"
    assert "output_config" not in messages.kwargs
    assert "thinking" not in messages.kwargs
    assert messages.kwargs["tool_choice"] == {"type": "tool", "name": "json_output"}

