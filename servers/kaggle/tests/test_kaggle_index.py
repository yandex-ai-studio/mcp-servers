import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "func-search" / "index.py"
SPEC = importlib.util.spec_from_file_location("kaggle_search_index", MODULE_PATH)
index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(index)


def _parse_body(response):
    assert response["statusCode"] == 200
    return json.loads(response["body"])


class FakeApi:
    def __init__(self, search_results=None, files_response=None, error=None):
        self.search_results = search_results or []
        self.files_response = files_response
        self.error = error
        self.list_kwargs = None
        self.file_calls = []

    def dataset_list(self, **kwargs):
        self.list_kwargs = kwargs
        if self.error:
            raise self.error
        return self.search_results

    def dataset_list_files(self, dataset_ref, page_size):
        self.file_calls.append((dataset_ref, page_size))
        return self.files_response


def _search_item(ref, title):
    return SimpleNamespace(
        ref=ref,
        title=title,
        subtitle=f"{title} subtitle",
        url=f"https://www.kaggle.com/datasets/{ref}",
        owner_name="Owner",
        owner_ref="owner",
        total_bytes=123,
        last_updated=datetime(2026, 1, 2, tzinfo=timezone.utc),
        download_count=12,
        view_count=34,
        vote_count=5,
        kernel_count=6,
        usability_rating=0.75,
        current_version_number=2,
        license_name="CC0: Public Domain",
        tags=[SimpleNamespace(ref="finance", name="Finance", description="Financial data", full_path="subject > finance")],
    )


def _detail(ref):
    return SimpleNamespace(
        ref=ref,
        description="Detailed description",
        topic_count=7,
        versions=[
            SimpleNamespace(
                version_number=2,
                creation_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                creator_name="Creator",
                creator_ref="creator",
                version_notes="Current version",
                status="Ready",
            )
        ],
    )


def _metadata():
    return SimpleNamespace(
        description="Metadata description",
        licenses=[SimpleNamespace(name="CC0-1.0")],
        keywords=["finance"],
        expected_update_frequency="annually",
    )


def test_handler_enriches_top_results_with_one_bounded_file_request(monkeypatch):
    first = _search_item("owner/first", "First")
    second = _search_item("owner/second", "Second")
    columns = [
        SimpleNamespace(order=0, name="id", type="integer", original_type="int64", description="Identifier")
    ]
    files_response = SimpleNamespace(
        dataset_files=[
            SimpleNamespace(
                name="table.csv",
                total_bytes=100,
                creation_date=datetime(2026, 1, 3, tzinfo=timezone.utc),
                description="Primary table",
                file_type="csv",
                url="https://example.test/table.csv",
                columns=columns,
            ),
            SimpleNamespace(
                name="readme.txt",
                total_bytes=10,
                creation_date=None,
                description="",
                file_type="",
                url="",
                columns=[],
            ),
        ],
        next_page_token="secret-next-page-token",
    )
    api = FakeApi([first, second], files_response)
    monkeypatch.setattr(index, "_get_api", lambda: api)
    monkeypatch.setattr(index, "_get_dataset_context", lambda _api, ref: (_detail(ref), _metadata()))

    response = index.handler(
        {
            "query": "credit risk",
            "max_results": 1,
            "max_files": 2,
            "sort_by": "votes",
            "file_type": "csv",
            "license": "cc",
            "min_size": 10,
            "max_size": 500,
        },
        None,
    )

    payload = _parse_body(response)
    assert payload["count"] == 1
    assert len(payload["items"]) == 1
    assert api.file_calls == [("owner/first", 2)]
    assert api.list_kwargs == {
        "search": "credit risk",
        "sort_by": "votes",
        "file_type": "csv",
        "license_name": "cc",
        "page": 1,
        "min_size": 10,
        "max_size": 500,
    }

    dataset = payload["items"][0]
    assert dataset["description"] == "Detailed description"
    assert dataset["licenses"] == ["CC0-1.0"]
    assert dataset["keywords"] == ["finance"]
    assert dataset["files_truncated"] is True
    assert dataset["files"][0]["columns"][0]["name"] == "id"
    assert "columns" not in dataset["files"][1]
    assert "secret-next-page-token" not in response["body"]


def test_handler_accepts_json_body_and_defaults(monkeypatch):
    item = _search_item("owner/data", "Data")
    files_response = SimpleNamespace(dataset_files=[], next_page_token="")
    api = FakeApi([item], files_response)
    monkeypatch.setattr(index, "_get_api", lambda: api)
    monkeypatch.setattr(index, "_get_dataset_context", lambda _api, ref: (_detail(ref), _metadata()))

    payload = _parse_body(index.handler({"body": json.dumps({"query": "data"})}, None))

    assert payload["count"] == 1
    assert api.file_calls == [("owner/data", 20)]
    assert api.list_kwargs["sort_by"] == "hottest"
    assert api.list_kwargs["page"] == 1


def test_empty_search_returns_empty_items(monkeypatch):
    api = FakeApi([])
    monkeypatch.setattr(index, "_get_api", lambda: api)

    payload = _parse_body(index.handler({"query": "nothing"}, None))

    assert payload == {"count": 0, "items": []}
    assert api.file_calls == []


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({}, "'query' is required and must be a non-empty string"),
        ({"query": "x", "max_results": 6}, "'max_results' must be <= 5"),
        ({"query": "x", "max_files": 101}, "'max_files' must be <= 100"),
        ({"query": "x", "sort_by": "relevance"}, "'sort_by' must be one of"),
        ({"query": "x", "file_type": "xlsx"}, "'file_type' must be one of"),
        ({"query": "x", "license": "private"}, "'license' must be one of"),
        ({"query": "x", "min_size": -1}, "'min_size' must be a non-negative integer"),
        ({"query": "x", "min_size": 20, "max_size": 10}, "'min_size' must be <= 'max_size'"),
    ],
)
def test_validation_errors(event, message):
    payload = _parse_body(index.handler(event, None))
    assert message in payload["error"]


def test_invalid_json_is_sanitized():
    payload = _parse_body(index.handler({"body": "{"}, None))
    assert payload == {"error": "invalid JSON in request body"}


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "Kaggle authentication failed"),
        (403, "Kaggle authentication failed"),
        (429, "Kaggle rate limit exceeded"),
        (503, "Kaggle request failed with HTTP status 503"),
    ],
)
def test_upstream_http_errors_are_sanitized(monkeypatch, status, message):
    error = RuntimeError("sensitive upstream URL and body")
    error.response = SimpleNamespace(status_code=status)
    monkeypatch.setattr(index, "_get_api", lambda: FakeApi(error=error))

    response = index.handler({"query": "data"}, None)
    payload = _parse_body(response)

    assert payload == {"error": message}
    assert "sensitive" not in response["body"]


def test_import_time_authentication_exit_is_sanitized(monkeypatch):
    def fail_auth():
        raise SystemExit(1)

    monkeypatch.setattr(index, "_get_api", fail_auth)
    payload = _parse_body(index.handler({"query": "data"}, None))
    assert payload == {"error": "Kaggle authentication failed"}
