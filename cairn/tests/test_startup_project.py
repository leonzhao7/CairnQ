from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cairn.dispatcher.llm import LLMError
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.startup_project import create_startup_project


class StubClient:
    def __init__(self, status_code: int = 201, data: dict[str, Any] | None = None):
        self.status_code = status_code
        self.data = data if data is not None else {"project": {"id": "proj_001"}}
        self.created: list[dict[str, Any]] = []

    def create_project(self, body: dict[str, Any]) -> ApiResult:
        self.created.append(body)
        return ApiResult(self.status_code, self.data, "")


@pytest.fixture(autouse=True)
def setup_logging(caplog):
    caplog.set_level(logging.INFO)


def _write_inputs(tmp_path: Path, *, prompt: str = "Task: test") -> tuple[Path, Path]:
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text(prompt)
    return llm_config, start_prompt


def test_create_startup_project_skips_when_no_llm_config(tmp_path, caplog):
    llm_config = tmp_path / "llm.json"
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: hack the box")
    client = StubClient()

    result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "No LLM config found" in caplog.text


def test_create_startup_project_skips_when_no_start_prompt(tmp_path, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    client = StubClient()

    result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "No start prompt found" in caplog.text


def test_create_startup_project_skips_when_prompt_empty(tmp_path, caplog):
    llm_config, start_prompt = _write_inputs(tmp_path, prompt="  \n  ")
    client = StubClient()

    result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "Start prompt is empty" in caplog.text


def test_create_startup_project_handles_invalid_llm_config(tmp_path, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"invalid": "json"')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: test")
    client = StubClient()

    result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "Failed to load LLM config" in caplog.text


def test_create_startup_project_handles_llm_error(tmp_path, caplog):
    llm_config, start_prompt = _write_inputs(tmp_path, prompt="Task: hack the planet")
    client = StubClient()

    with patch("cairn.dispatcher.startup_project.chat") as mock_chat:
        mock_chat.side_effect = LLMError("connection timeout")
        result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "LLM call failed" in caplog.text


def test_create_startup_project_handles_invalid_json_response(tmp_path, caplog):
    llm_config, start_prompt = _write_inputs(tmp_path)
    client = StubClient()

    with patch("cairn.dispatcher.startup_project.chat") as mock_chat:
        mock_chat.return_value = "not json at all"
        result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "Failed to parse LLM response" in caplog.text


def test_create_startup_project_handles_missing_required_fields(tmp_path, caplog):
    llm_config, start_prompt = _write_inputs(tmp_path)
    client = StubClient()

    with patch("cairn.dispatcher.startup_project.chat") as mock_chat:
        mock_chat.return_value = '{"title": "test", "origin": "here"}'
        result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert client.created == []
    assert "Failed to parse LLM response" in caplog.text


def test_create_startup_project_posts_parsed_project_to_server(tmp_path, caplog):
    llm_config, start_prompt = _write_inputs(
        tmp_path,
        prompt="Pentest target 10.0.0.5, get root shell",
    )
    client = StubClient()

    llm_response = '''
    {
        "title": "Pentest 10.0.0.5",
        "origin": "Target: 10.0.0.5",
        "goal": "Obtain root shell on 10.0.0.5",
        "hints": [
            {"content": "Start with port scan", "creator": "llm"},
            "Check for web vulnerabilities"
        ],
        "bootstrap_enabled": true
    }
    '''

    with patch("cairn.dispatcher.startup_project.chat") as mock_chat:
        mock_chat.return_value = llm_response
        result = create_startup_project(client, llm_config, start_prompt)

    assert result == "proj_001"
    assert "Created startup project proj_001" in caplog.text
    assert client.created == [
        {
            "title": "Pentest 10.0.0.5",
            "origin": "Target: 10.0.0.5",
            "goal": "Obtain root shell on 10.0.0.5",
            "bootstrap_enabled": True,
            "hints": [
                {"content": "Start with port scan", "creator": "llm"},
                {"content": "Check for web vulnerabilities", "creator": "llm"},
            ],
        }
    ]


def test_create_startup_project_uses_defaults_for_missing_optional_fields(tmp_path):
    llm_config, start_prompt = _write_inputs(tmp_path)
    client = StubClient()

    with patch("cairn.dispatcher.startup_project.chat") as mock_chat:
        mock_chat.return_value = '{"origin": "start", "goal": "finish"}'
        result = create_startup_project(client, llm_config, start_prompt)

    assert result == "proj_001"
    assert client.created == [
        {
            "title": "Startup Project",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": True,
            "hints": None,
        }
    ]


def test_create_startup_project_handles_server_error(tmp_path, caplog):
    llm_config, start_prompt = _write_inputs(tmp_path)
    client = StubClient(status_code=500)

    with patch("cairn.dispatcher.startup_project.chat") as mock_chat:
        mock_chat.return_value = '{"origin": "start", "goal": "finish"}'
        result = create_startup_project(client, llm_config, start_prompt)

    assert result is None
    assert "Failed to create startup project" in caplog.text


def test_loop_startup_project_runs_only_once(tmp_path):
    from cairn.dispatcher.scheduler.loop import DispatcherLoop

    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.client = object()
    loop.llm_config_path = tmp_path / "llm.json"
    loop.start_prompt_path = tmp_path / "start_prompt.md"
    loop.startup_project_enabled = True
    loop._startup_project_attempted = False

    with patch(
        "cairn.dispatcher.scheduler.loop.create_startup_project",
        return_value="proj_001",
    ) as mock_create:
        assert loop._maybe_create_startup_project() == "proj_001"
        assert loop._maybe_create_startup_project() is None

    mock_create.assert_called_once_with(loop.client, loop.llm_config_path, loop.start_prompt_path)


def test_loop_startup_project_skipped_when_not_configured():
    from cairn.dispatcher.scheduler.loop import DispatcherLoop

    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.startup_project_enabled = False
    loop._startup_project_attempted = False

    with patch("cairn.dispatcher.scheduler.loop.create_startup_project") as mock_create:
        assert loop._maybe_create_startup_project() is None

    mock_create.assert_not_called()


def test_llm_config_user_agent_is_sent_in_headers(tmp_path):
    from unittest.mock import MagicMock

    from cairn.dispatcher.llm import LLMConfig, chat

    config = LLMConfig(
        url="http://test",
        api_key="key",
        model="model",
        user_agent="TestAgent/1.0"
    )

    with patch("cairn.dispatcher.llm.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "test response"}}]
        }
        mock_post.return_value = mock_response

        chat(config, [{"role": "user", "content": "test"}])

        call_args = mock_post.call_args
        headers = call_args[1]["headers"]
        assert "User-Agent" in headers
        assert headers["User-Agent"] == "TestAgent/1.0"
