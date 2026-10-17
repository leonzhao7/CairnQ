from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cairn.server import db
from cairn.server.llm import LLMConfig, LLMError, chat
from cairn.server.models import CreateHintInline, CreateProjectRequest
from cairn.server.output_parser import extract_json_object
from cairn.server.services import insert_project

DEFAULT_LLM_CONFIG = Path("llm.json")
DEFAULT_START_PROMPT = Path("start_prompt.md")

logger = logging.getLogger(__name__)


def create_startup_project(
    llm_config_path: Path = DEFAULT_LLM_CONFIG,
    start_prompt_path: Path = DEFAULT_START_PROMPT,
) -> str | None:
    """Read start_prompt.md, send it to the LLM, parse the response, and insert a project.

    Returns the new project id on success, or None if skipped or failed.
    Logs rather than raises — this is a best-effort feature that should not block server startup.
    """
    if not llm_config_path.exists():
        logger.info("No LLM config found at %s, skipping startup project creation", llm_config_path)
        return None
    if not start_prompt_path.exists():
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

    system_prompt = _load_system_prompt()
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

    try:
        with db.get_conn() as conn:
            detail = insert_project(conn, project_request)
    except Exception as exc:
        logger.error("Failed to insert startup project: %s", exc)
        return None

    logger.info("Created startup project %s: %s", detail.project.id, detail.project.title)
    return detail.project.id


def _load_system_prompt() -> str:
    return (
        resources.files("cairn.server.prompts")
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
