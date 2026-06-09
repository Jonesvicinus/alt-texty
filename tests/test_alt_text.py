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


# --- /generate-meta route tests ---

def test_generate_meta_missing_url_returns_400(client):
    resp = client.post("/generate-meta", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "page_url is required"


def test_generate_meta_empty_url_returns_400(client):
    resp = client.post("/generate-meta", json={"page_url": "  "})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "page_url is required"


def test_generate_meta_unsafe_url_returns_400(client):
    for bad_url in ["http://127.0.0.1/admin", "http://localhost/admin"]:
        resp = client.post("/generate-meta",
                           json={"page_url": bad_url})
        assert resp.status_code == 400
        assert "invalid_url" in resp.get_json()["error"]


def test_generate_meta_openai_failure_returns_error_json(client):
    with patch("alt_text.OpenAI") as mock_openai, \
         patch("alt_text._requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Hello world page content</body></html>"
        mock_requests.get.return_value = mock_resp

        mock_openai.return_value.chat.completions.create.side_effect = Exception("quota exceeded")

        resp = client.post("/generate-meta", json={"page_url": "https://example.com/"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["error"] == "openai_failed"
        assert "quota exceeded" in data["message"]


def test_generate_meta_success_returns_structured_result(client):
    with patch("alt_text.OpenAI") as mock_openai, \
         patch("alt_text._requests") as mock_requests:
        mock_page = MagicMock()
        mock_page.text = "<html><body>Buy our amazing running shoes online.</body></html>"
        mock_requests.get.return_value = mock_page

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "recommended": "Shop premium running shoes with free shipping. Top brands, expert reviews, and 30-day returns.",
            "rationale": "Leads with the primary action and includes trust signals.",
            "confidence": "high"
        })
        mock_openai.return_value.chat.completions.create.return_value.choices = [mock_choice]

        resp = client.post("/generate-meta", json={
            "page_url": "https://example.com/shoes",
            "field": "meta",
            "current_title": "Running Shoes",
            "h1": "Buy Running Shoes"
        })
        data = resp.get_json()
        assert data["recommended"].startswith("Shop premium")
        assert data["confidence"] == "high"
        assert "char_count" in data
        assert isinstance(data["warnings"], list)


def test_generate_title_success_returns_structured_result(client):
    with patch("alt_text.OpenAI") as mock_openai, \
         patch("alt_text._requests") as mock_requests:
        mock_page = MagicMock()
        mock_page.text = "<html><body>Running shoes page</body></html>"
        mock_requests.get.return_value = mock_page

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "recommended": "Running Shoes | Free Shipping | Example",
            "rationale": "Short, keyword-forward, includes brand.",
            "confidence": "high"
        })
        mock_openai.return_value.chat.completions.create.return_value.choices = [mock_choice]

        resp = client.post("/generate-meta", json={
            "page_url": "https://example.com/shoes",
            "field": "title"
        })
        data = resp.get_json()
        assert "Running Shoes" in data["recommended"]
        assert data["char_count"] == len(data["recommended"])


def test_generate_meta_warns_when_too_long(client):
    long_meta = "A" * 165
    with patch("alt_text.OpenAI") as mock_openai, \
         patch("alt_text._requests") as mock_requests:
        mock_page = MagicMock()
        mock_page.text = "<html><body>content</body></html>"
        mock_requests.get.return_value = mock_page

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "recommended": long_meta,
            "rationale": "test",
            "confidence": "low"
        })
        mock_openai.return_value.chat.completions.create.return_value.choices = [mock_choice]

        resp = client.post("/generate-meta", json={"page_url": "https://example.com/", "field": "meta"})
        data = resp.get_json()
        assert data["warnings"]  # non-empty
        assert any("trim" in w for w in data["warnings"])
