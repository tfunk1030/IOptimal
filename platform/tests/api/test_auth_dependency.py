import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.dependencies import get_request_context
from api.models.database import DatabaseGateway


def _request(host: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": (host, 12345),
        "server": ("test", 80),
    }
    return Request(scope)


def test_localhost_request_allows_dev_context_without_token():
    db = DatabaseGateway()
    ctx = get_request_context(
        request=_request("127.0.0.1"),
        authorization=None,
        x_dev_driver_id="dev-driver",
        x_dev_team_id="dev-team",
        db=db,
    )
    assert ctx.user_id == "dev-driver"
    assert ctx.team_id == "dev-team"
    assert ctx.is_local_dev is True


def test_remote_request_requires_bearer_token():
    db = DatabaseGateway()
    with pytest.raises(HTTPException) as exc:
        get_request_context(
            request=_request("203.0.113.2"),
            authorization=None,
            x_dev_driver_id=None,
            x_dev_team_id=None,
            db=db,
        )
    assert exc.value.status_code == 401

