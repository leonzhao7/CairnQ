from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from cairn.dispatcher.llm import LLMConfig, LLMError, chat
from cairn.dispatcher.output_parser import extract_json_object
from cairn.dispatcher.protocol.client import CairnClient
from cairn.server.models import CreateHintInline, CreateProjectRequest

DEFAULT_LLM_CONFIG = Path("llm.json")
DEFAULT_START_PROMPT = Path("start_prompt.md")

logger = logging.getLogger(__name__)


def create_startup_project(
    client: CairnClient,
    llm_config_path: Path = DEFAULT_LLM_CONFIG,
    start_prompt_path: Path = DEFAULT_START_PROMPT,
) -> str | None:
    """Read start_prompt.md, send it to the LLM, parse the response, and create a project.

    The project is created through the server's HTTP API; the dispatcher never touches the
    database directly. Returns the new project id on success, or None if skipped or failed.
    Logs rather than raises — this is a best-effort feature that should not block dispatcher startup.
    """
    if not llm_config_path.is_file():
        logger.info("No LLM config found at %s, skipping startup project creation", llm_config_path)
        return None
    if not start_prompt_path.is_file():
        logger.info("No start prompt found at %s, skipping startup project creation", start_prompt_path)
        return None

    try:
        llm_config = LLMConfig.load(llm_config_path)
    except (OSError, ValueError, ValidationError) as exc:
        logger.error("Failed to load LLM config from %s: %s", llm_config_path, exc)
        return None

    try:
        user_prompt = start_prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.error("Failed to read start prompt from %s: %s", start_prompt_path, exc)
        return None

    if not user_prompt:
        logger.info("Start prompt is empty, skipping startup project creation")
        return None

    try:
        system_prompt = _load_system_prompt()
    except OSError as exc:
        logger.error("Failed to load startup project system prompt: %s", exc)
        return None
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("Calling LLM to extract project structure from start prompt (%s)", llm_config.describe())
    try:
        response_text = chat(llm_config, messages)
    except LLMError as exc:
        logger.error("LLM call failed: %s", exc)
        return None

    try:
        project_request = _parse_project_request(response_text)
    except (ValueError, ValidationError) as exc:
        logger.error("Failed to parse LLM response into project: %s", exc)
        logger.debug("LLM response text: %s", response_text[:500])
        return None

    result = client.create_project(project_request.model_dump())
    if not result.ok:
        logger.error(
            "Failed to create startup project: HTTP %s %s",
            result.status_code,
            result.text[:300],
        )
        return None

    project_id = None
    if isinstance(result.data, dict):
        project = result.data.get("project")
        if isinstance(project, dict):
            project_id = project.get("id")
    if not isinstance(project_id, str) or not project_id:
        logger.error("Server response for startup project has no project id: %s", result.text[:300])
        return None

    logger.info("Created startup project %s: %s", project_id, project_request.title)
    return project_id


def _load_system_prompt() -> str:
    return (
        resources.files("cairn.dispatcher.prompts")
        .joinpath("startup_project.md")
        .read_text(encoding="utf-8")
    )


def _parse_project_request(text: str) -> CreateProjectRequest:
    """Extract the JSON object and normalize into a CreateProjectRequest."""
    raw = extract_json_object(text)
    if not isinstance(raw, dict):
        raise ValueError("LLM response is not a JSON object")

    title = raw.get("title", "Startup Project").strip() or "Startup Project"
    origin = raw.get("origin", "").strip()
    goal = raw.get("goal", "").strip()
    bootstrap_enabled = bool(raw.get("bootstrap_enabled", True))

    if not origin or not goal:
        raise ValueError("LLM response missing required origin or goal")

    hints_raw = raw.get("hints")
    hints: list[CreateHintInline] | None = None
    if hints_raw is not None:
        if not isinstance(hints_raw, list):
            raise ValueError("hints must be an array")
        hints = []
        for item in hints_raw:
            if isinstance(item, str):
                content = item.strip()
                if content:
                    hints.append(CreateHintInline(content=content, creator="llm"))
            elif isinstance(item, dict):
                content = item.get("content", "").strip()
                creator = item.get("creator", "llm").strip() or "llm"
                if content:
                    hints.append(CreateHintInline(content=content, creator=creator))

    return CreateProjectRequest(
        title=title,
        origin=origin,
        goal=goal,
        bootstrap_enabled=bootstrap_enabled,
        hints=hints,
    )
