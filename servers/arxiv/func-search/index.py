import json
import os
from typing import Any, Dict

import feedparser
import requests

ARXIV_API_URL = os.getenv("ARXIV_API_URL", "http://export.arxiv.org/api/query")
REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "15"))
MAX_RESULTS_DEFAULT = 3
MAX_RESULTS_LIMIT = 25

FIELD_MAP = {
    "all": "all",
    "title": "ti",
    "abstract": "abs",
    "author": "au",
}

SORT_BY_ALLOWED = {"relevance", "submittedDate", "lastUpdatedDate"}
SORT_ORDER_ALLOWED = {"ascending", "descending"}


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


def _optional_positive_int(payload: Dict[str, Any], field_name: str, default: int, max_value: int) -> int:
    value = payload.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"'{field_name}' must be a positive integer")
    if value > max_value:
        raise ValueError(f"'{field_name}' must be <= {max_value}")
    return value


def _optional_enum(payload: Dict[str, Any], field_name: str, default: str, allowed: set) -> str:
    value = payload.get(field_name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string")
    normalized = value.strip()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"'{field_name}' must be one of: {choices}")
    return normalized


def _sanitize_summary(summary: str) -> str:
    return " ".join((summary or "").replace("\n", " ").split())


def _extract_arxiv_id(entry: Any) -> str:
    raw_id = str(getattr(entry, "id", "") or "")
    if "/abs/" in raw_id:
        return raw_id.split("/abs/")[-1]
    return raw_id


def _extract_links(entry: Any) -> Dict[str, str]:
    abs_url = ""
    pdf_url = ""
    for link in getattr(entry, "links", []) or []:
        href = str(getattr(link, "href", "") or "")
        rel = str(getattr(link, "rel", "") or "")
        title = str(getattr(link, "title", "") or "")
        if rel == "alternate" and href:
            abs_url = href
        if title == "pdf" and href:
            pdf_url = href
    return {"abs_url": abs_url, "pdf_url": pdf_url}


def _entry_to_paper(entry: Any) -> Dict[str, Any]:
    authors = []
    for author in getattr(entry, "authors", []) or []:
        name = str(getattr(author, "name", "") or "").strip()
        if name:
            authors.append(name)

    categories = []
    for tag in getattr(entry, "tags", []) or []:
        term = getattr(tag, "term", "")
        if isinstance(term, str) and term.strip():
            categories.append(term.strip())

    return {
        "arxiv_id": _extract_arxiv_id(entry),
        "title": str(getattr(entry, "title", "") or "").strip(),
        "authors": authors,
        "summary": _sanitize_summary(str(getattr(entry, "summary", "") or "")),
        "published": str(getattr(entry, "published", "") or ""),
        "updated": str(getattr(entry, "updated", "") or ""),
        "categories": categories,
        "links": _extract_links(entry),
    }


def _extract_total_results(feed: Any) -> int:
    feed_info = getattr(feed, "feed", {}) or {}
    raw = feed_info.get("opensearch_totalresults")
    try:
        return int(raw)
    except Exception:
        return len(getattr(feed, "entries", []) or [])


def _arxiv_query(params: Dict[str, Any]) -> Any:
    headers = {"User-Agent": "arxiv-search-cloud-function/1.0"}
    response = requests.get(ARXIV_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
    response.raise_for_status()
    return feedparser.parse(response.content)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    del context

    try:
        payload = _parse_event_payload(event)
        query = _require_non_empty_string(payload, "query")
        field = _optional_enum(payload, "field", "all", set(FIELD_MAP.keys()))
        max_results = _optional_positive_int(payload, "max_results", MAX_RESULTS_DEFAULT, MAX_RESULTS_LIMIT)
        sort_by = _optional_enum(payload, "sort_by", "relevance", SORT_BY_ALLOWED)
        sort_order = _optional_enum(payload, "sort_order", "descending", SORT_ORDER_ALLOWED)

        search_query = f"{FIELD_MAP[field]}:{query.replace(' ', '+')}"
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        feed = _arxiv_query(params)
        entries = getattr(feed, "entries", []) or []
        papers = [_entry_to_paper(entry) for entry in entries]
        return _json_response(
            {
                "total_results": _extract_total_results(feed),
                "count": len(papers),
                "items": papers,
            }
        )
    except json.JSONDecodeError:
        return _error("invalid JSON in request body")
    except ValueError as exc:
        return _error(str(exc))
    except requests.RequestException as exc:
        return _error(f"arXiv request failed: {exc}")
    except Exception as exc:
        return _error(f"internal error: {exc}")
