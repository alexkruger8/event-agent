"""
Provider-agnostic LLM client.

Supports Anthropic and OpenAI. Tool definitions are always passed in Anthropic format
(with `input_schema`); this module translates them for OpenAI internally.

Auto-detects provider from settings: Anthropic if ANTHROPIC_API_KEY is set, else OpenAI.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


class LLMClient:
    def __init__(self, provider: str, api_key: str, model: str) -> None:
        self.provider = provider
        self.model = model
        if provider == "anthropic":
            import anthropic
            self._client: Any = anthropic.Anthropic(api_key=api_key)
        elif provider == "openai":
            import openai
            self._client = openai.OpenAI(api_key=api_key)
        else:
            raise ValueError(f"Unknown provider: {provider!r}")

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        """Simple single-turn completion. Returns text."""
        if self.provider == "anthropic":
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return str(text)
        else:
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            return str(resp.choices[0].message.content or "")

    def complete_json(
        self,
        system: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """Completion that returns a JSON object as a string.

        Pass `schema` (a JSON Schema dict) for best results. On Anthropic this
        uses a forced tool call so the request still ends with a user message.
        On OpenAI it enables json_schema response_format.
        """
        if self.provider == "anthropic":
            if schema is not None:
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[
                        {
                            "name": "json_output",
                            "description": "Return the requested response as structured JSON.",
                            "input_schema": schema,
                        }
                    ],
                    tool_choice={"type": "tool", "name": "json_output"},
                )
                tool_input = next(
                    (
                        b.input
                        for b in resp.content
                        if b.type == "tool_use" and b.name == "json_output"
                    ),
                    None,
                )
                if tool_input is not None:
                    return json.dumps(tool_input)
            else:
                json_system = system + "\n\nRespond with a valid JSON object only. No markdown fences, no other text."
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=json_system,
                    messages=[{"role": "user", "content": prompt}],
                )
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return str(text)
        else:
            json_system = system + "\n\nRespond with a valid JSON object only. No markdown fences, no other text."
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": json_system},
                    {"role": "user", "content": prompt},
                ],
            }
            if schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema, "strict": True},
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}
            resp = self._client.chat.completions.create(**kwargs)
            return str(resp.choices[0].message.content or "")

    def _tools_for_provider(self, tools: list[dict[str, Any]]) -> list[Any]:
        """Convert Anthropic-format tool defs (input_schema) to provider format."""
        if self.provider == "anthropic":
            return tools
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    def call_with_tools(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> Any:
        """Make one API call with tools. Returns the raw provider response."""
        provider_tools = self._tools_for_provider(tools)
        if self.provider == "anthropic":
            return self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                tools=provider_tools,
                messages=messages,
            )
        else:
            oai_messages = [{"role": "system", "content": system}] + messages
            return self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                tools=provider_tools,
                messages=oai_messages,
            )

    def is_done(self, response: Any) -> bool:
        """Return True when the model has finished generating (no pending tool calls)."""
        if self.provider == "anthropic":
            return bool(response.stop_reason == "end_turn")
        return response.choices[0].finish_reason in ("stop", None)

    def parse_response(self, response: Any) -> tuple[str | None, list[ToolCall]]:
        """Extract the assistant text and any tool calls from a response."""
        if self.provider == "anthropic":
            text: str | None = next(
                (b.text for b in response.content if b.type == "text"), None
            )
            calls = [
                ToolCall(id=b.id, name=b.name, input=dict(b.input))
                for b in response.content
                if b.type == "tool_use"
            ]
            return text, calls
        else:
            msg = response.choices[0].message
            oai_text: str | None = msg.content or None
            calls = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments),
                    ))
            return oai_text, calls

    def append_assistant(self, messages: list[dict[str, Any]], response: Any) -> None:
        """Append the assistant turn (including tool calls if any) to the messages list."""
        if self.provider == "anthropic":
            messages.append({"role": "assistant", "content": response.content})
        else:
            msg = response.choices[0].message
            entry: dict[str, Any] = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(entry)

    def append_tool_results(
        self,
        messages: list[dict[str, Any]],
        results: list[tuple[str, str]],
    ) -> None:
        """Append tool results. results is a list of (tool_call_id, content) pairs."""
        if self.provider == "anthropic":
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": content}
                    for tid, content in results
                ],
            })
        else:
            for tid, content in results:
                messages.append({"role": "tool", "tool_call_id": tid, "content": content})


def get_llm_client() -> LLMClient:
    """Return an LLMClient configured from settings. Raises if no key is set."""
    from app.config import settings

    if settings.anthropic_api_key:
        return LLMClient("anthropic", settings.anthropic_api_key, settings.anthropic_model)
    if settings.openai_api_key:
        return LLMClient("openai", settings.openai_api_key, settings.openai_model)
    raise ValueError(
        "No LLM API key configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your .env file."
    )
