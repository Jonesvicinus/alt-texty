import json
import pytest
from unittest.mock import patch, MagicMock
from flask import Flask


@pytest.fixture
def app():
    from alt_text import alt_text_bp
    flask_app = Flask(__name__)
    flask_app.register_blueprint(alt_text_bp)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# --- _validate tests (pure logic, no HTTP) ---

def test_validate_clean_result_has_no_warnings():
    from alt_text import _validate
    result = {
        "role": "informative",
        "recommended_alt": "Team members collaborating at a whiteboard",
        "rationale": "The image shows people working together",
        "confidence": "high"
    }
    assert _validate(result) == []


def test_validate_flags_image_of_prefix():
    from alt_text import _validate
    result = {
        "role": "informative",
        "recommended_alt": "Image of a mountain range at sunset",
        "rationale": "...",
        "confidence": "high"
    }
    warnings = _validate(result)
    assert any("prefix" in w for w in warnings)


def test_validate_flags_photo_of_prefix():
    from alt_text import _validate
    result = {
        "role": "informative",
        "recommended_alt": "photo of a dog",
        "rationale": "...",
        "confidence": "medium"
    }
    warnings = _validate(result)
    assert any("prefix" in w for w in warnings)


def test_validate_flags_over_200_chars():
    from alt_text import _validate
    result = {
        "role": "informative",
        "recommended_alt": "x" * 201,
        "rationale": "...",
        "confidence": "low"
    }
    warnings = _validate(result)
    assert any("201" in w or "characters" in w for w in warnings)


def test_validate_flags_decorative_with_nonempty_alt():
    from alt_text import _validate
    result = {
        "role": "decorative",
        "recommended_alt": "A decorative swirl pattern",
        "rationale": "...",
        "confidence": "high"
    }
    warnings = _validate(result)
    assert any("empty" in w for w in warnings)


def test_validate_decorative_with_empty_alt_is_clean():
    from alt_text import _validate
    result = {
        "role": "decorative",
        "recommended_alt": "",
        "rationale": "purely decorative",
        "confidence": "high"
    }
    assert _validate(result) == []


# --- /generate-alt route tests ---

def test_generate_alt_missing_src_returns_400(client):
    response = client.post("/generate-alt", json={})
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "src is required" in data["error"]


def test_generate_alt_empty_src_returns_400(client):
    response = client.post("/generate-alt", json={"src": "   "})
    assert response.status_code == 400


def test_generate_alt_image_fetch_failure_returns_error_json(client):
    with patch("alt_text._requests.get") as mock_get:
        mock_get.side_effect = Exception("Connection timed out")
        response = client.post("/generate-alt", json={"src": "https://example.com/img.jpg"})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["error"] == "image_fetch_failed"
    assert "Connection timed out" in data["message"]


def test_generate_alt_success_returns_structured_result(client):
    mock_img = MagicMock()
    mock_img.content = b"fake-jpeg-bytes"
    mock_img.headers = {"content-type": "image/jpeg"}
    mock_img.raise_for_status = MagicMock()

    openai_result = {
        "role": "informative",
        "recommended_alt": "Team members collaborating at a whiteboard",
        "rationale": "Shows people working together in an office setting",
        "confidence": "high"
    }
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(openai_result)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    with patch("alt_text._requests.get", return_value=mock_img), \
         patch("alt_text.OpenAI") as mock_openai_class:
        mock_openai_class.return_value.chat.completions.create.return_value = mock_completion
        response = client.post("/generate-alt", json={
            "src": "https://example.com/team.jpg",
            "page_url": "https://example.com/about",
            "classification": "missing"
        })

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["role"] == "informative"
    assert data["recommended_alt"] == "Team members collaborating at a whiteboard"
    assert data["confidence"] == "high"
    assert data["warnings"] == []


def test_generate_alt_openai_failure_returns_error_json(client):
    mock_img = MagicMock()
    mock_img.content = b"fake-jpeg-bytes"
    mock_img.headers = {"content-type": "image/jpeg"}
    mock_img.raise_for_status = MagicMock()

    with patch("alt_text._requests.get", return_value=mock_img), \
         patch("alt_text.OpenAI") as mock_openai_class:
        mock_openai_class.return_value.chat.completions.create.side_effect = Exception("quota exceeded")
        response = client.post("/generate-alt", json={"src": "https://example.com/img.jpg"})

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["error"] == "openai_failed"
    assert "quota exceeded" in data["message"]


def test_generate_alt_result_includes_warnings_for_bad_alt(client):
    mock_img = MagicMock()
    mock_img.content = b"fake-jpeg-bytes"
    mock_img.headers = {"content-type": "image/jpeg"}
    mock_img.raise_for_status = MagicMock()

    openai_result = {
        "role": "informative",
        "recommended_alt": "Image of a mountain range",
        "rationale": "...",
        "confidence": "medium"
    }
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(openai_result)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    with patch("alt_text._requests.get", return_value=mock_img), \
         patch("alt_text.OpenAI") as mock_openai_class:
        mock_openai_class.return_value.chat.completions.create.return_value = mock_completion
        response = client.post("/generate-alt", json={"src": "https://example.com/mountain.jpg"})

    data = json.loads(response.data)
    assert len(data["warnings"]) > 0
    assert any("prefix" in w for w in data["warnings"])
