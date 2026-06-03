import os
import base64
import json
import requests as _requests
from flask import Blueprint, request, jsonify
from openai import OpenAI
from urllib.parse import urlparse

_ALLOWED_IMAGE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif"
})


def _is_safe_image_url(url: str) -> bool:
    import ipaddress
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            return False
    except ValueError:
        pass  # hostname, not an IP literal
    if host == "localhost":
        return False
    return True


alt_text_bp = Blueprint('alt_text', __name__)
_openai_api_key = os.environ.get("OPENAI_API_KEY")
_openai_client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None

_SYSTEM_PROMPT = """You are an accessibility expert. Classify the image role and generate WCAG 2.1 AA compliant alt text following success criterion 1.1.1.

Image roles:
- informative: conveys meaningful content — describe the information conveyed, not visual appearance
- functional: inside a link or button — describe the destination or action, e.g. "Go to homepage"
- decorative: purely decorative, no informational value — recommended_alt MUST be empty string ""
- complex: chart, graph, infographic, map — summarise the key takeaway

Rules for recommended_alt:
- Never start with "image of", "photo of", "picture of", or "graphic of"
- Aim for under 125 characters
- For decorative images, recommended_alt must be exactly empty string ""
- For functional images, describe what happens when clicked, not the visual
"""

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "alt_text_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["informative", "functional", "decorative", "complex"]
                },
                "recommended_alt": {"type": "string"},
                "rationale": {"type": "string"},
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"]
                }
            },
            "required": ["role", "recommended_alt", "rationale", "confidence"],
            "additionalProperties": False
        }
    }
}


def _validate(result):
    """Return a list of warning strings for a generated alt text result."""
    warnings = []
    alt = result.get("recommended_alt", "")
    role = result.get("role", "")

    for prefix in ("image of", "photo of", "picture of", "graphic of"):
        if alt.lower().startswith(prefix):
            warnings.append("Alt text starts with a redundant prefix — remove it")
            break

    if len(alt) > 200:
        warnings.append(
            f"Alt text is {len(alt)} characters — consider shortening to under 200"
        )

    if role == "decorative" and alt:
        warnings.append('Decorative images should have empty alt text (alt="")')

    return warnings


@alt_text_bp.route("/generate-alt", methods=["POST"])
def generate_alt():
    data = request.get_json(silent=True) or {}
    src = (data.get("src") or "").strip()
    if not src:
        return jsonify({"error": "src is required"}), 400

    page_url = data.get("page_url", "")
    classification = data.get("classification", "missing")
    in_link = bool(data.get("in_link", False))
    surrounding_text = data.get("surrounding_text", "")

    if not _is_safe_image_url(src):
        return jsonify({"error": "invalid_image_url", "message": "URL must be http/https and not point to a local address"}), 400

    # Fetch image bytes
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
    try:
        img_resp = _requests.get(
            src, timeout=5, headers={"User-Agent": "Mozilla/5.0"}, stream=True
        )
        img_resp.raise_for_status()
        content_type = (
            img_resp.headers.get("content-type", "image/jpeg")
            .split(";")[0]
            .strip()
        )
        if content_type not in _ALLOWED_IMAGE_TYPES:
            return jsonify({
                "error": "unsupported_image_type",
                "message": f"Content-Type '{content_type}' is not a supported image type"
            }), 415
        chunks, total = [], 0
        for chunk in img_resp.iter_content(65536):
            total += len(chunk)
            if total > _MAX_IMAGE_BYTES:
                return jsonify({
                    "error": "image_too_large",
                    "message": "Image exceeds 5 MB limit"
                }), 413
            chunks.append(chunk)
        image_b64 = base64.standard_b64encode(b"".join(chunks)).decode("utf-8")
    except Exception as exc:
        return jsonify({"error": "image_fetch_failed", "message": str(exc)}), 200

    # Build context for the model
    ctx_lines = []
    if page_url:
        ctx_lines.append(f"Page URL: {page_url}")
    if in_link:
        ctx_lines.append(
            "This image is inside a link or button — treat as functional."
        )
    if surrounding_text:
        ctx_lines.append(f"Surrounding text on the page: {surrounding_text}")
    ctx_lines.append(f"Crawler classification: {classification}")
    user_text = "\n".join(ctx_lines)

    # Call OpenAI
    try:
        _client = _openai_client or OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        completion = _client.chat.completions.create(
            model="gpt-4o",
            response_format=_RESPONSE_FORMAT,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{content_type};base64,{image_b64}"
                            }
                        }
                    ]
                }
            ]
        )
    except Exception as exc:
        return jsonify({"error": "openai_failed", "message": str(exc)}), 200

    result = json.loads(completion.choices[0].message.content)
    result["warnings"] = _validate(result)
    return jsonify(result)


_META_SYSTEM_PROMPT = """You are an SEO copywriter. Generate a compelling, click-worthy meta description for a web page.

Rules:
- 140–160 characters (absolute max 160)
- Describe what the page delivers — specific and actionable, not vague
- Include the primary keyword naturally, near the front if possible
- Write for a human scanning search results, not for a crawler
- No keyword stuffing. No generic filler ("This page is about…", "Learn more…")
- Do not use quotation marks in the output

Return JSON only: {"recommended": "...", "rationale": "...", "confidence": "high|medium|low"}"""

_TITLE_SYSTEM_PROMPT = """You are an SEO copywriter. Generate a compelling title tag for a web page.

Rules:
- 50–60 characters (absolute max 65)
- Primary keyword near the front
- Human-readable, not stuffed
- If the brand name fits, append it after a pipe: "Keyword-rich title | Brand"
- Do not use quotation marks in the output

Return JSON only: {"recommended": "...", "rationale": "...", "confidence": "high|medium|low"}"""


@alt_text_bp.route("/generate-meta", methods=["POST"])
def generate_meta():
    data = request.get_json(silent=True) or {}
    page_url = (data.get("page_url") or "").strip()
    field = data.get("field", "meta")  # "meta" or "title"

    if not page_url:
        return jsonify({"error": "page_url is required"}), 400

    # Validate URL is safe (same guard as images)
    if not _is_safe_image_url(page_url):
        return jsonify({"error": "invalid_url", "message": "URL must be http/https and not point to a local address"}), 400

    # Fetch page to extract text context
    body_text = ""
    try:
        resp = _requests.get(
            page_url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BowstSEO/1.0)"},
            allow_redirects=True
        )
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for el in soup(["script", "style", "nav", "footer", "header"]):
            el.decompose()
        body_text = " ".join(soup.get_text(separator=" ").split())[:2500]
    except Exception:
        pass

    ctx_parts = [f"Page URL: {page_url}"]
    if data.get("current_title"):
        ctx_parts.append(f"Current title tag: {data['current_title']}")
    if data.get("current_meta") and field == "title":
        ctx_parts.append(f"Current meta description: {data['current_meta']}")
    if data.get("h1"):
        ctx_parts.append(f"H1: {data['h1']}")
    if body_text:
        ctx_parts.append(f"Page text excerpt:\n{body_text}")

    system_prompt = _META_SYSTEM_PROMPT if field == "meta" else _TITLE_SYSTEM_PROMPT

    try:
        client = _openai_client or OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            max_tokens=250,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "\n".join(ctx_parts)}
            ]
        )
    except Exception as exc:
        return jsonify({"error": "openai_failed", "message": str(exc)}), 200

    result = json.loads(completion.choices[0].message.content)
    recommended = result.get("recommended", "")
    n = len(recommended)
    warnings = []
    if field == "meta":
        if n > 160:
            warnings.append(f"{n} chars — trim to under 160")
        elif n < 70:
            warnings.append(f"{n} chars — aim for 140–160")
    else:
        if n > 65:
            warnings.append(f"{n} chars — trim to under 65")
        elif n < 30:
            warnings.append(f"{n} chars — aim for 50–60")
    result["warnings"] = warnings
    result["char_count"] = n
    return jsonify(result)
