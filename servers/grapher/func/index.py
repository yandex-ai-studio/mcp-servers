import base64
import io
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SUPPORTED_GRAPH_TYPES = {"bar", "line", "pie"}
DEFAULT_DPI = 100
DEFAULT_WIDTH = 10
DEFAULT_HEIGHT = 6
DEFAULT_PIE_HEIGHT = 8
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


def _generate_object_key():
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex
    return f"grapher/{now.strftime('%Y/%m/%d')}/{stamp}-{suffix}.png"


def _upload_to_bucket(image_bytes):
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
        Body=image_bytes,
        ContentType="image/png",
        ACL="public-read",
    )
    return f"{S3_ENDPOINT_URL}/{bucket}/{object_key}"


def _require_graph_type(payload):
    value = payload.get("graph_type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'graph_type' is required and must be a non-empty string")
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_GRAPH_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_GRAPH_TYPES))
        raise ValueError(f"unsupported graph_type '{normalized}', expected one of: {allowed}")
    return normalized


def _require_object(payload, field_name):
    value = payload.get(field_name)
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' is required and must be an object")
    return value


def _read_options(payload, graph_type):
    options = payload.get("options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("'options' must be an object when provided")

    title = options.get("title")
    if title is None:
        title = "Graph"
    if not isinstance(title, str):
        raise ValueError("'options.title' must be a string when provided")

    xlabel = options.get("xlabel", "")
    ylabel = options.get("ylabel", "")
    if not isinstance(xlabel, str):
        raise ValueError("'options.xlabel' must be a string when provided")
    if not isinstance(ylabel, str):
        raise ValueError("'options.ylabel' must be a string when provided")

    width = options.get("width", DEFAULT_WIDTH)
    height_default = DEFAULT_PIE_HEIGHT if graph_type == "pie" else DEFAULT_HEIGHT
    height = options.get("height", height_default)
    dpi = options.get("dpi", DEFAULT_DPI)

    if isinstance(width, bool) or not isinstance(width, (int, float)) or width <= 0:
        raise ValueError("'options.width' must be a positive number when provided")
    if isinstance(height, bool) or not isinstance(height, (int, float)) or height <= 0:
        raise ValueError("'options.height' must be a positive number when provided")
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("'options.dpi' must be a positive integer when provided")

    return {
        "title": title,
        "xlabel": xlabel,
        "ylabel": ylabel,
        "width": float(width),
        "height": float(height),
        "dpi": dpi,
    }


def _validate_string_list(value, field_name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"'{field_name}' must be a non-empty array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"'{field_name}' must contain non-empty strings")
    return [item.strip() for item in value]


def _validate_number_list(value, field_name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"'{field_name}' must be a non-empty array")
    validated = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"'{field_name}' must contain only numbers")
        validated.append(float(item))
    return validated


def _validate_line_x_list(value):
    if not isinstance(value, list) or not value:
        raise ValueError("'data.x_values' must be a non-empty array")
    kinds = set()
    normalized = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("'data.x_values' must contain numbers or strings")
        if isinstance(item, (int, float)):
            kinds.add("number")
            normalized.append(float(item))
            continue
        if isinstance(item, str) and item.strip():
            kinds.add("string")
            normalized.append(item.strip())
            continue
        raise ValueError("'data.x_values' must contain numbers or non-empty strings")

    if len(kinds) > 1:
        raise ValueError("'data.x_values' must contain values of one type: all numbers or all strings")

    return normalized


def _draw_bar(data, options):
    labels = _validate_string_list(data.get("labels"), "data.labels")
    values = _validate_number_list(data.get("values"), "data.values")
    if len(labels) != len(values):
        raise ValueError("'data.labels' and 'data.values' must have the same length")

    fig, ax = plt.subplots(figsize=(options["width"], options["height"]), dpi=options["dpi"])
    bars = ax.bar(labels, values, color="steelblue", edgecolor="black")
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{val:g}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_title(options["title"], fontsize=14, fontweight="bold")
    ax.set_xlabel(options["xlabel"])
    ax.set_ylabel(options["ylabel"] or "Value")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig


def _draw_line(data, options):
    x_values = _validate_line_x_list(data.get("x_values"))
    y_values = _validate_number_list(data.get("y_values"), "data.y_values")
    if len(x_values) != len(y_values):
        raise ValueError("'data.x_values' and 'data.y_values' must have the same length")

    fig, ax = plt.subplots(figsize=(options["width"], options["height"]), dpi=options["dpi"])
    ax.plot(x_values, y_values, marker="o", linewidth=2, markersize=6, color="steelblue")
    ax.set_title(options["title"], fontsize=14, fontweight="bold")
    ax.set_xlabel(options["xlabel"] or "X")
    ax.set_ylabel(options["ylabel"] or "Y")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def _draw_pie(data, options):
    labels = _validate_string_list(data.get("labels"), "data.labels")
    values = _validate_number_list(data.get("values"), "data.values")
    if len(labels) != len(values):
        raise ValueError("'data.labels' and 'data.values' must have the same length")
    if any(v < 0 for v in values):
        raise ValueError("'data.values' for pie chart must be non-negative")
    if sum(values) <= 0:
        raise ValueError("'data.values' for pie chart must sum to a positive value")

    fig, ax = plt.subplots(figsize=(options["width"], options["height"]), dpi=options["dpi"])
    ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 10},
    )
    ax.set_title(options["title"], fontsize=14, fontweight="bold")
    fig.tight_layout()
    return fig


def _render_graph(graph_type, data, options):
    if graph_type == "bar":
        return _draw_bar(data, options)
    if graph_type == "line":
        return _draw_line(data, options)
    if graph_type == "pie":
        return _draw_pie(data, options)
    raise ValueError(f"unsupported graph_type '{graph_type}'")


def _render_png_bytes(fig):
    stream = io.BytesIO()
    fig.savefig(stream, format="png")
    return stream.getvalue()


def _build_success_payload(image_bytes):
    response_mode = _read_response_mode()
    if response_mode == "inline":
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        return {"image_url": f"data:image/png;base64,{image_base64}"}

    image_url = _upload_to_bucket(image_bytes=image_bytes)
    return {"image_url": image_url}


def handler(event, context):
    del context

    try:
        payload = _parse_event_payload(event)
    except json.JSONDecodeError:
        return _error("invalid JSON in request body")
    except ValueError as exc:
        return _error(str(exc))

    fig = None
    try:
        graph_type = _require_graph_type(payload)
        data = _require_object(payload, "data")
        options = _read_options(payload, graph_type)

        fig = _render_graph(graph_type, data, options)
        image_bytes = _render_png_bytes(fig)
        response_payload = _build_success_payload(image_bytes=image_bytes)
        return _json_response(response_payload)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"internal error: {exc}")
    finally:
        if fig is not None:
            plt.close(fig)
