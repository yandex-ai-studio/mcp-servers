import json
import os
from typing import Any, Dict

import feedparser
import requests

ARXIV_API_URL = os.getenv("ARXIV_API_URL", "http://export.arxiv.org/api/query")
REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "15"))


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


def _normalize_arxiv_id(raw_arxiv_id: str) -> str:
    return raw_arxiv_id.replace("arXiv:", "").strip()


def _arxiv_query(params: Dict[str, Any]) -> Any:
    headers = {"User-Agent": "arxiv-get-cloud-function/1.0"}
    response = requests.get(ARXIV_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
    response.raise_for_status()
    return feedparser.parse(response.content)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    del context

    try:
        payload = _parse_event_payload(event)
        arxiv_id = _normalize_arxiv_id(_require_non_empty_string(payload, "arxiv_id"))

        params = {
            "id_list": arxiv_id,
            "max_results": 1,
        }
        feed = _arxiv_query(params)
        entries = getattr(feed, "entries", []) or []
        if not entries:
            return _error(f"paper with arxiv_id '{arxiv_id}' was not found")
        return _json_response({"paper": _entry_to_paper(entries[0])})
    except json.JSONDecodeError:
        return _error("invalid JSON in request body")
    except ValueError as exc:
        return _error(str(exc))
    except requests.RequestException as exc:
        return _error(f"arXiv request failed: {exc}")
    except Exception as exc:
        return _error(f"internal error: {exc}")
