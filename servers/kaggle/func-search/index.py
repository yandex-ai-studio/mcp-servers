import json
import os
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple


MAX_RESULTS_DEFAULT = 3
MAX_RESULTS_LIMIT = 5
MAX_FILES_DEFAULT = 20
MAX_FILES_LIMIT = 100

SORT_BY_ALLOWED = {"hottest", "votes", "updated", "active", "published"}
FILE_TYPE_ALLOWED = {"all", "csv", "sqlite", "json", "bigQuery", "parquet"}
LICENSE_ALLOWED = {"all", "cc", "gpl", "odb", "other"}

_API = None
KAGGLE_CONFIG_DIR = "/tmp/.kaggle"


def _json_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _error(message: str) -> Dict[str, Any]:
    return _json_response({"error": message})


def _parse_event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")

    body = event.get("body")
    if isinstance(body, str):
        if not body.strip():
            raise ValueError("request body is empty")
        payload = json.loads(body)
    elif isinstance(body, dict):
        payload = body
    else:
        payload = event

    if not isinstance(payload, dict):
        raise ValueError("request payload must be a JSON object")
    return payload


def _require_non_empty_string(payload: Dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' is required and must be a non-empty string")
    return value.strip()


def _optional_positive_int(payload: Dict[str, Any], field_name: str, default: int, maximum: int) -> int:
    value = payload.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"'{field_name}' must be a positive integer")
    if value > maximum:
        raise ValueError(f"'{field_name}' must be <= {maximum}")
    return value


def _optional_non_negative_int(payload: Dict[str, Any], field_name: str) -> Optional[int]:
    value = payload.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"'{field_name}' must be a non-negative integer")
    return value


def _optional_enum(
    payload: Dict[str, Any], field_name: str, default: Optional[str], allowed: set
) -> Optional[str]:
    value = payload.get(field_name, default)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string")
    normalized = value.strip()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"'{field_name}' must be one of: {choices}")
    return normalized


def _get_api() -> Any:
    global _API
    if _API is None:
        # Kaggle creates its config directory while importing. Yandex Cloud
        # Functions mounts the application directory read-only, so keep the
        # client's writable state in the function's temporary filesystem.
        os.environ["KAGGLE_CONFIG_DIR"] = KAGGLE_CONFIG_DIR
        from kaggle import api as kaggle_api

        _API = kaggle_api
    return _API


def _get_dataset_context(api: Any, dataset_ref: str) -> Tuple[Any, Any]:
    from kagglesdk.datasets.types.dataset_api_service import (
        ApiGetDatasetMetadataRequest,
        ApiGetDatasetRequest,
    )

    owner_slug, dataset_slug, _ = api.split_dataset_string(dataset_ref)

    detail_request = ApiGetDatasetRequest()
    detail_request.owner_slug = owner_slug
    detail_request.dataset_slug = dataset_slug

    metadata_request = ApiGetDatasetMetadataRequest()
    metadata_request.owner_slug = owner_slug
    metadata_request.dataset_slug = dataset_slug

    with api.build_kaggle_client() as client:
        detail = client.datasets.dataset_api_client.get_dataset(detail_request)
        metadata_response = client.datasets.dataset_api_client.get_dataset_metadata(metadata_request)

    error_message = getattr(metadata_response, "error_message", "")
    if error_message:
        raise RuntimeError("Kaggle metadata request failed")
    return detail, getattr(metadata_response, "info", None)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _first(*values: Any) -> Any:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple, dict)) and not value:
            continue
        return value
    return None


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        rendered = value.isoformat()
        if rendered.endswith("+00:00"):
            rendered = rendered[:-6] + "Z"
        return rendered
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _compact_object(obj: Any, fields: Iterable[str]) -> Dict[str, Any]:
    result = {}
    for field in fields:
        value = _scalar(_attr(obj, field))
        if value is not None and value != "":
            result[field] = value
    return result


def _columns(file_obj: Any) -> List[Dict[str, Any]]:
    columns = _attr(file_obj, "columns", None) or []
    return [
        _compact_object(column, ("order", "name", "type", "original_type", "description"))
        for column in columns
        if column is not None
    ]


def _file_item(file_obj: Any) -> Dict[str, Any]:
    item = _compact_object(
        file_obj,
        ("name", "total_bytes", "creation_date", "description", "file_type", "url"),
    )
    columns = _columns(file_obj)
    if columns:
        item["columns"] = columns
    return item


def _tag_item(tag: Any) -> Dict[str, Any]:
    return _compact_object(tag, ("ref", "name", "description", "full_path"))


def _version_item(version: Any) -> Dict[str, Any]:
    return _compact_object(
        version,
        ("version_number", "creation_date", "creator_name", "creator_ref", "version_notes", "status"),
    )


def _license_names(metadata: Any) -> List[str]:
    names = []
    for license_obj in _attr(metadata, "licenses", None) or []:
        name = _attr(license_obj, "name", "")
        if name:
            names.append(str(name))
    return names


def _dataset_item(search_item: Any, detail: Any, metadata: Any, files_response: Any) -> Dict[str, Any]:
    dataset_ref = str(_first(_attr(detail, "ref"), _attr(search_item, "ref")) or "")
    tags = _first(_attr(detail, "tags"), _attr(search_item, "tags")) or []
    versions = _attr(detail, "versions", None) or []
    files = _first(
        _attr(files_response, "dataset_files"),
        _attr(files_response, "files"),
    ) or []

    item = {
        "ref": dataset_ref,
        "url": _first(
            _attr(detail, "url"),
            _attr(search_item, "url"),
            f"https://www.kaggle.com/datasets/{dataset_ref}" if dataset_ref else None,
        ),
        "title": _first(_attr(detail, "title"), _attr(search_item, "title")),
        "subtitle": _first(_attr(detail, "subtitle"), _attr(search_item, "subtitle")),
        "description": _first(
            _attr(detail, "description"),
            _attr(metadata, "description"),
            _attr(search_item, "description"),
        ),
        "owner_name": _first(_attr(detail, "owner_name"), _attr(search_item, "owner_name")),
        "owner_ref": _first(_attr(detail, "owner_ref"), _attr(search_item, "owner_ref")),
        "creator_name": _first(_attr(detail, "creator_name"), _attr(search_item, "creator_name")),
        "total_bytes": _first(_attr(detail, "total_bytes"), _attr(search_item, "total_bytes")),
        "last_updated": _first(_attr(detail, "last_updated"), _attr(search_item, "last_updated")),
        "download_count": _first(_attr(detail, "download_count"), _attr(search_item, "download_count")),
        "view_count": _first(_attr(detail, "view_count"), _attr(search_item, "view_count")),
        "vote_count": _first(_attr(detail, "vote_count"), _attr(search_item, "vote_count")),
        "kernel_count": _first(_attr(detail, "kernel_count"), _attr(search_item, "kernel_count")),
        "topic_count": _attr(detail, "topic_count"),
        "usability_rating": _first(
            _attr(detail, "usability_rating"), _attr(search_item, "usability_rating")
        ),
        "current_version_number": _first(
            _attr(detail, "current_version_number"),
            _attr(search_item, "current_version_number"),
        ),
        "license_name": _first(_attr(detail, "license_name"), _attr(search_item, "license_name")),
        "licenses": _license_names(metadata),
        "keywords": list(_attr(metadata, "keywords", None) or []),
        "expected_update_frequency": _attr(metadata, "expected_update_frequency"),
        "tags": [_tag_item(tag) for tag in tags if tag is not None],
        "versions": [_version_item(version) for version in versions if version is not None],
        "files": [_file_item(file_obj) for file_obj in files if file_obj is not None],
        "files_truncated": bool(_attr(files_response, "next_page_token", "")),
    }

    return {key: _scalar(value) if not isinstance(value, (list, dict)) else value for key, value in item.items() if value is not None and value != ""}


def _upstream_error(exc: Exception) -> Dict[str, Any]:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (401, 403):
        return _error("Kaggle authentication failed")
    if status == 429:
        return _error("Kaggle rate limit exceeded")
    if isinstance(status, int):
        return _error(f"Kaggle request failed with HTTP status {status}")
    return _error("Kaggle request failed")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    del context

    try:
        payload = _parse_event_payload(event)
        query = _require_non_empty_string(payload, "query")
        max_results = _optional_positive_int(
            payload, "max_results", MAX_RESULTS_DEFAULT, MAX_RESULTS_LIMIT
        )
        max_files = _optional_positive_int(payload, "max_files", MAX_FILES_DEFAULT, MAX_FILES_LIMIT)
        sort_by = _optional_enum(payload, "sort_by", "hottest", SORT_BY_ALLOWED)
        file_type = _optional_enum(payload, "file_type", None, FILE_TYPE_ALLOWED)
        license_name = _optional_enum(payload, "license", None, LICENSE_ALLOWED)
        min_size = _optional_non_negative_int(payload, "min_size")
        max_size = _optional_non_negative_int(payload, "max_size")
        if min_size is not None and max_size is not None and min_size > max_size:
            raise ValueError("'min_size' must be <= 'max_size'")

        api = _get_api()
        search_results = api.dataset_list(
            search=query,
            sort_by=sort_by,
            file_type=file_type,
            license_name=license_name,
            page=1,
            min_size=min_size,
            max_size=max_size,
        ) or []

        items = []
        for search_item in [item for item in search_results if item is not None][:max_results]:
            dataset_ref = _attr(search_item, "ref", "")
            if not dataset_ref:
                raise RuntimeError("Kaggle returned a dataset without a reference")
            detail, metadata = _get_dataset_context(api, dataset_ref)
            files_response = api.dataset_list_files(dataset_ref, page_size=max_files)
            items.append(_dataset_item(search_item, detail, metadata, files_response))

        return _json_response({"count": len(items), "items": items})
    except json.JSONDecodeError:
        return _error("invalid JSON in request body")
    except ValueError as exc:
        return _error(str(exc))
    except SystemExit:
        return _error("Kaggle authentication failed")
    except Exception as exc:
        return _upstream_error(exc)
