import base64
import io
import json
import math
import os
import uuid
from datetime import datetime, timezone

import boto3
from PIL import Image
from yandex_ai_studio_sdk import AIStudio


DEFAULT_WIDTH_RATIO = 1
DEFAULT_HEIGHT_RATIO = 1
DEFAULT_MODEL = "yandex-art"
DEFAULT_DOWNSIZE_FACTOR = 1.0
DEFAULT_RESPONSE_MODE = "inline"
S3_ENDPOINT_URL = "https://storage.yandexcloud.net"


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
    return value.strip()


def _read_optional_string(payload, field_name):
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string when provided")
    return value.strip()


def _read_positive_number(payload, field_name, default_value):
    value = payload.get(field_name, default_value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"'{field_name}' must be a positive number when provided")

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    raise ValueError(f"'{field_name}' must be a positive integer when provided")


def _detect_mime_type(image_bytes):
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpeg"
    return "application/octet-stream", "bin"


def _read_downsize_factor():
    raw = os.environ.get("DOWNSIZE_FACTOR")
    if raw is None or not raw.strip():
        return DEFAULT_DOWNSIZE_FACTOR

    try:
        value = float(raw.strip())
    except ValueError:
        raise ValueError("'DOWNSIZE_FACTOR' must be a positive number >= 1")

    if not math.isfinite(value) or value < 1:
        raise ValueError("'DOWNSIZE_FACTOR' must be a positive number >= 1")
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


def _generate_object_key(image_format):
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    ext = image_format if image_format in {"jpeg", "png"} else "bin"
    suffix = uuid.uuid4().hex
    return f"yart/{now.strftime('%Y/%m/%d')}/{stamp}-{suffix}.{ext}"


def _upload_to_bucket(image_bytes, mime_type, image_format):
    static_key_id = _require_env_non_empty("static_key_id")
    static_key_secret = _require_env_non_empty("static_key_secret")
    bucket = _require_env_non_empty("bucket")

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=static_key_id,
        aws_secret_access_key=static_key_secret,
    )
    object_key = _generate_object_key(image_format=image_format)
    s3.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=image_bytes,
        ContentType=mime_type,
        ACL="public-read",
    )
    image_url = f"{S3_ENDPOINT_URL}/{bucket}/{object_key}"
    return image_url


def _downsize_image(image_bytes, downsize_factor):
    if downsize_factor == 1:
        return image_bytes

    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        new_width = max(1, int(math.floor(width / downsize_factor)))
        new_height = max(1, int(math.floor(height / downsize_factor)))

        if new_width == width and new_height == height:
            return image_bytes

        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        output = io.BytesIO()

        image_format = image.format
        if not image_format:
            _, detected = _detect_mime_type(image_bytes)
            if detected == "jpeg":
                image_format = "JPEG"
            elif detected == "png":
                image_format = "PNG"
            else:
                image_format = "PNG"

        save_kwargs = {}
        if image_format.upper() == "JPEG" and resized.mode in ("RGBA", "P"):
            resized = resized.convert("RGB")
            save_kwargs["quality"] = 95
        resized.save(output, format=image_format, **save_kwargs)
        return output.getvalue()


def _generate_image(prompt, folder_id, api_key, width_ratio, height_ratio):
    sdk = AIStudio(folder_id=folder_id, auth=api_key)
    model = sdk.models.image_generation(DEFAULT_MODEL)
    model = model.configure(width_ratio=width_ratio, height_ratio=height_ratio)
    operation = model.run_deferred([prompt])
    result = operation.wait()
    image_bytes = getattr(result, "image_bytes", None)
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("image generation returned empty image bytes")
    return bytes(image_bytes)


def _resolve_credentials(payload):
    request_folder_id = _read_optional_string(payload, "folder_id")
    request_api_key = _read_optional_string(payload, "api_key")

    folder_id = request_folder_id or os.environ.get("YART_FOLDER_ID", "").strip()
    api_key = request_api_key or os.environ.get("YART_API_KEY", "").strip()

    if not folder_id:
        raise ValueError(
            "'folder_id' is required unless YART_FOLDER_ID is configured in function environment"
        )
    if not api_key:
        raise ValueError(
            "'api_key' is required unless YART_API_KEY is configured in function environment"
        )
    return folder_id, api_key


def _build_success_payload(image_bytes):
    response_mode = _read_response_mode()
    mime_type, image_format = _detect_mime_type(image_bytes)

    if response_mode == "inline":
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        return {"image_url": f"data:{mime_type};base64,{image_base64}"}

    image_url = _upload_to_bucket(
        image_bytes=image_bytes,
        mime_type=mime_type,
        image_format=image_format,
    )
    return {"image_url": image_url}


def handler(event, context):
    del context

    try:
        payload = _parse_event_payload(event)
    except json.JSONDecodeError:
        return _error("invalid JSON in request body")
    except ValueError as exc:
        return _error(str(exc))

    try:
        prompt = _require_string(payload, "prompt")
        folder_id, api_key = _resolve_credentials(payload)
        width_ratio = _read_positive_number(payload, "width_ratio", DEFAULT_WIDTH_RATIO)
        height_ratio = _read_positive_number(payload, "height_ratio", DEFAULT_HEIGHT_RATIO)
        downsize_factor = _read_downsize_factor()

        image_bytes = _generate_image(
            prompt=prompt,
            folder_id=folder_id,
            api_key=api_key,
            width_ratio=width_ratio,
            height_ratio=height_ratio,
        )
        image_bytes = _downsize_image(image_bytes=image_bytes, downsize_factor=downsize_factor)
        response_payload = _build_success_payload(image_bytes=image_bytes)
        return _json_response(response_payload)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"internal error: {exc}")
