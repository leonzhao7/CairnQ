from __future__ import annotations

import requests

from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.startup_healthcheck import (
    StartupHealthcheckResult,
    format_failure_summary,
)


def test_client_request_failure_returns_status_zero() -> None:
    class Session:
        def request(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

    client = CairnClient("http://server/")
    client._local.session = Session()

    result = client.create_intent("proj_001", ["f001"], "investigate", "reasoner")

    assert result.status_code == 0
    assert result.text == "offline"


def test_client_create_project_posts_to_projects_endpoint() -> None:
    class Response:
        status_code = 201
        headers = {"content-type": "application/json"}
        text = '{"project": {"id": "proj_001"}}'

        def json(self):
            return {"project": {"id": "proj_001"}}

    class Session:
        def __init__(self):
            self.calls: list[tuple[str, str, dict]] = []

        def request(self, method, url, json, timeout):
            self.calls.append((method, url, json))
            return Response()

    client = CairnClient("http://server/")
    session = Session()
    client._local.session = session
    body = {"title": "test", "origin": "start", "goal": "finish"}

    result = client.create_project(body)

    assert result.status_code == 201
    assert result.data == {"project": {"id": "proj_001"}}
    assert session.calls == [("POST", "http://server/projects", body)]


def test_startup_healthcheck_failure_summary_includes_worker_details() -> None:
    results = [
        StartupHealthcheckResult(
            worker_name="worker-a",
            ok=False,
            status=401,
            duration_ms=12,
            detail="unauthorized",
            endpoint="POST http://api/v1/messages",
        ),
        StartupHealthcheckResult(
            worker_name="worker-b",
            ok=True,
            status=200,
            duration_ms=8,
            detail="",
            endpoint="POST http://api/v1/messages",
        ),
    ]

    summary = format_failure_summary(results)

    assert summary == (
        "startup healthchecks failed for all workers: worker-a(http=401, detail=unauthorized)"
    )
