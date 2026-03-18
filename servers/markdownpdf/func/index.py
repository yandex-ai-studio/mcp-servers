import base64
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3


DEFAULT_RESPONSE_MODE = "inline"
DEFAULT_FONT_DIR = "/function/storage/fonts"
S3_ENDPOINT_URL = "https://storage.yandexcloud.net"
REQUIRED_FONT_FILES = (
    "DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf",
    "DejaVuSans-Oblique.ttf",
    "DejaVuSans-BoldOblique.ttf",
)


def _json_response(payload):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _error(message):
    return _json_response({"error": message})


def _parse_event_payload(event):
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


def _require_string(payload, field_name):
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' is required and must be a non-empty string")
    return value


def _read_response_mode():
    raw = os.environ.get("mode")
    if raw is None or not raw.strip():
        return DEFAULT_RESPONSE_MODE

    mode = raw.strip().lower()
    if mode not in {"inline", "bucket"}:
        raise ValueError("'mode' must be either 'inline' or 'bucket'")
    return mode


def _require_env_non_empty(key_name):
    value = os.environ.get(key_name)
    if value is None or not value.strip():
        raise ValueError(f"'{key_name}' is required in function environment for selected mode")
    return value.strip()


def _read_font_directory():
    raw = os.environ.get("MARKDOWNPDF_FONT_DIR")
    if raw is None or not raw.strip():
        return Path(DEFAULT_FONT_DIR)
    return Path(raw.strip())


def _require_fonts_available(font_directory):
    if not font_directory.exists():
        raise ValueError(f"font directory does not exist: {font_directory}")
    if not font_directory.is_dir():
        raise ValueError(f"font directory is not a directory: {font_directory}")

    missing = [name for name in REQUIRED_FONT_FILES if not (font_directory / name).is_file()]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(
            f"missing required font files in '{font_directory}': {missing_list}"
        )


def _generate_object_key():
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex
    return f"markdownpdf/{now.strftime('%Y/%m/%d')}/{stamp}-{suffix}.pdf"


def _upload_to_bucket(pdf_bytes):
    static_key_id = _require_env_non_empty("static_key_id")
    static_key_secret = _require_env_non_empty("static_key_secret")
    bucket = _require_env_non_empty("bucket")

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=static_key_id,
        aws_secret_access_key=static_key_secret,
    )
    object_key = _generate_object_key()
    s3.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ACL="public-read",
    )
    return f"{S3_ENDPOINT_URL}/{bucket}/{object_key}"


def _render_pdf_bytes(markdown_text):
    from simpdf import FontFace, MarkdownPdfRenderer

    font_directory = _read_font_directory()
    _require_fonts_available(font_directory)

    renderer = MarkdownPdfRenderer(
        font_directory=font_directory,
        font_face=FontFace.dejavu_sans(),
    )
    pdf_bytes = renderer.render_to_bytes(markdown_text)
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise ValueError("markdown rendering returned empty PDF bytes")

    pdf_bytes = bytes(pdf_bytes)
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("markdown rendering did not return a PDF document")
    return pdf_bytes


def _build_success_payload(pdf_bytes):
    response_mode = _read_response_mode()
    if response_mode == "inline":
        pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
        return {"pdf_url": f"data:application/pdf;base64,{pdf_base64}"}

    pdf_url = _upload_to_bucket(pdf_bytes=pdf_bytes)
    return {"pdf_url": pdf_url}


def handler(event, context):
    del context

    try:
        payload = _parse_event_payload(event)
    except json.JSONDecodeError:
        return _error("invalid JSON in request body")
    except ValueError as exc:
        return _error(str(exc))

    try:
        markdown_text = _require_string(payload, "markdown")
        pdf_bytes = _render_pdf_bytes(markdown_text)
        response_payload = _build_success_payload(pdf_bytes=pdf_bytes)
        return _json_response(response_payload)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"internal error: {exc}")
