import base64
import importlib.util
import json
import sys
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "func" / "index.py"
SPEC = importlib.util.spec_from_file_location("markdownpdf_index", MODULE_PATH)
index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(index)


def _parse_body(response):
    return json.loads(response["body"])


def test_handler_accepts_direct_payload_inline(monkeypatch):
    monkeypatch.setenv("mode", "inline")
    monkeypatch.setattr(index, "_render_pdf_bytes", lambda markdown: b"%PDF-test")

    response = index.handler({"markdown": "# Hello"}, None)

    payload = _parse_body(response)
    assert payload["pdf_url"].startswith("data:application/pdf;base64,")
    encoded = payload["pdf_url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"%PDF-test"


def test_handler_accepts_body_payload(monkeypatch):
    monkeypatch.setenv("mode", "inline")
    monkeypatch.setattr(index, "_render_pdf_bytes", lambda markdown: b"%PDF-body")

    response = index.handler({"body": json.dumps({"markdown": "# Hello"})}, None)

    payload = _parse_body(response)
    assert payload["pdf_url"].startswith("data:application/pdf;base64,")


def test_handler_rejects_missing_markdown():
    response = index.handler({}, None)
    payload = _parse_body(response)
    assert payload["error"] == "'markdown' is required and must be a non-empty string"


def test_handler_rejects_invalid_json():
    response = index.handler({"body": "{"}, None)
    payload = _parse_body(response)
    assert payload["error"] == "invalid JSON in request body"


def test_bucket_mode_returns_uploaded_url(monkeypatch):
    captured = {}

    monkeypatch.setenv("mode", "bucket")
    monkeypatch.setattr(index, "_render_pdf_bytes", lambda markdown: b"%PDF-bucket")

    def fake_upload(pdf_bytes):
        captured["pdf_bytes"] = pdf_bytes
        return "https://storage.yandexcloud.net/test-bucket/file.pdf"

    monkeypatch.setattr(index, "_upload_to_bucket", fake_upload)

    response = index.handler({"markdown": "# Hello"}, None)

    payload = _parse_body(response)
    assert payload["pdf_url"] == "https://storage.yandexcloud.net/test-bucket/file.pdf"
    assert captured["pdf_bytes"] == b"%PDF-bucket"


def test_require_fonts_available_reports_missing_files(tmp_path):
    tmp_path.mkdir(exist_ok=True)

    try:
        index._require_fonts_available(tmp_path)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "DejaVuSans.ttf" in str(exc)


def test_render_pdf_bytes_uses_simpdf_api(monkeypatch, tmp_path):
    for filename in index.REQUIRED_FONT_FILES:
        (tmp_path / filename).write_bytes(b"font")

    captured = {}

    class FakeFontFace:
        @classmethod
        def dejavu_sans(cls):
            return "dejavu-face"

    class FakeRenderer:
        def __init__(self, font_directory, font_face):
            captured["font_directory"] = Path(font_directory)
            captured["font_face"] = font_face

        def render_to_bytes(self, markdown_text):
            captured["markdown_text"] = markdown_text
            return b"%PDF-fake"

    fake_simpdf = types.SimpleNamespace(
        FontFace=FakeFontFace,
        MarkdownPdfRenderer=FakeRenderer,
    )

    monkeypatch.setenv("MARKDOWNPDF_FONT_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "simpdf", fake_simpdf)

    pdf_bytes = index._render_pdf_bytes("# Привет")

    assert pdf_bytes == b"%PDF-fake"
    assert captured["font_directory"] == tmp_path
    assert captured["font_face"] == "dejavu-face"
    assert captured["markdown_text"] == "# Привет"
