from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from cairn.server import db
from cairn.server.llm import LLMError
from cairn.server.startup_project import create_startup_project


@pytest.fixture
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "test.db")
    yield


@pytest.fixture(autouse=True)
def setup_logging(caplog):
    caplog.set_level(logging.INFO)


def test_create_startup_project_skips_when_no_llm_config(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: hack the box")

    result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "No LLM config found" in caplog.text


def test_create_startup_project_skips_when_no_start_prompt(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"

    result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "No start prompt found" in caplog.text


def test_create_startup_project_skips_when_prompt_empty(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("  \n  ")

    result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "Start prompt is empty" in caplog.text


def test_create_startup_project_handles_invalid_llm_config(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"invalid": "json"')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: test")

    result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "Failed to load LLM config" in caplog.text


def test_create_startup_project_handles_llm_error(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: hack the planet")

    with patch("cairn.server.startup_project.chat") as mock_chat:
        mock_chat.side_effect = LLMError("connection timeout")
        result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "LLM call failed" in caplog.text


def test_create_startup_project_handles_invalid_json_response(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: test")

    with patch("cairn.server.startup_project.chat") as mock_chat:
        mock_chat.return_value = "not json at all"
        result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "Failed to parse LLM response" in caplog.text


def test_create_startup_project_handles_missing_required_fields(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task: test")

    with patch("cairn.server.startup_project.chat") as mock_chat:
        mock_chat.return_value = '{"title": "test", "origin": "here"}'
        result = create_startup_project(llm_config, start_prompt)

    assert result is None
    assert "Failed to parse LLM response" in caplog.text


def test_create_startup_project_success(tmp_path, setup_db, caplog):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Pentest target 10.0.0.5, get root shell")

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

    with patch("cairn.server.startup_project.chat") as mock_chat:
        mock_chat.return_value = llm_response
        result = create_startup_project(llm_config, start_prompt)

    assert result is not None
    assert result == "proj_001"
    assert "Created startup project proj_001" in caplog.text

    with db.get_conn() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (result,)).fetchone()
        assert project["title"] == "Pentest 10.0.0.5"
        assert project["status"] == "active"
        assert project["bootstrap_enabled"] == 1

        facts = conn.execute("SELECT * FROM facts WHERE project_id = ?", (result,)).fetchall()
        assert len(facts) == 2
        fact_dict = {f["id"]: f["description"] for f in facts}
        assert fact_dict["origin"] == "Target: 10.0.0.5"
        assert fact_dict["goal"] == "Obtain root shell on 10.0.0.5"

        hints = conn.execute("SELECT * FROM hints WHERE project_id = ?", (result,)).fetchall()
        assert len(hints) == 2
        assert hints[0]["content"] == "Start with port scan"
        assert hints[0]["creator"] == "llm"
        assert hints[1]["content"] == "Check for web vulnerabilities"
        assert hints[1]["creator"] == "llm"


def test_create_startup_project_uses_defaults_for_missing_optional_fields(tmp_path, setup_db):
    llm_config = tmp_path / "llm.json"
    llm_config.write_text('{"url": "http://api", "api_key": "k", "model": "m"}')
    start_prompt = tmp_path / "start_prompt.md"
    start_prompt.write_text("Task")

    llm_response = '{"origin": "start", "goal": "finish"}'

    with patch("cairn.server.startup_project.chat") as mock_chat:
        mock_chat.return_value = llm_response
        result = create_startup_project(llm_config, start_prompt)

    assert result is not None

    with db.get_conn() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (result,)).fetchone()
        assert project["title"] == "Startup Project"
        assert project["bootstrap_enabled"] == 1

        hints = conn.execute("SELECT * FROM hints WHERE project_id = ?", (result,)).fetchall()
        assert len(hints) == 0


def test_llm_config_user_agent_is_sent_in_headers(tmp_path):
    from unittest.mock import MagicMock, patch
    from cairn.server.llm import LLMConfig, chat

    config = LLMConfig(
        url="http://test",
        api_key="key",
        model="model",
        user_agent="TestAgent/1.0"
    )

    with patch("cairn.server.llm.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "test response"}}]
        }
        mock_post.return_value = mock_response

        chat(config, [{"role": "user", "content": "test"}])

        # Verify User-Agent was included in headers
        call_args = mock_post.call_args
        headers = call_args[1]["headers"]
        assert "User-Agent" in headers
        assert headers["User-Agent"] == "TestAgent/1.0"
