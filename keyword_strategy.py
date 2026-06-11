import os
import json
import re
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify

strategy_bp = Blueprint('keyword_strategy', __name__)

_STRATEGIES_DIR = os.path.expanduser('~/.site-crawler-crawls/keyword-strategies')


def _safe_slug(path: str) -> str:
    slug = path.strip('/')
    slug = re.sub(r'[^a-zA-Z0-9_-]', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or 'home'


def _domain_from_url(url: str):
    try:
        parsed = urlparse(url if '://' in url else f'https://{url}')
        host = (parsed.netloc or parsed.path).lower()
        host = host.split('/')[0].split(':')[0]
        if host.startswith('www.'):
            host = host[4:]
        return host or None
    except Exception:
        return None


def _safe_domain(domain: str) -> str:
    return re.sub(r'[^a-zA-Z0-9._-]', '_', domain)


def _strategy_dir(domain: str) -> str:
    return os.path.join(_STRATEGIES_DIR, _safe_domain(domain))


def _strategy_path(domain: str) -> str:
    return os.path.join(_strategy_dir(domain), 'strategy.json')


def _pages_dir(domain: str) -> str:
    return os.path.join(_strategy_dir(domain), 'pages')


def load_strategy(domain: str):
    path = _strategy_path(domain)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _save_strategy(domain: str, data: dict) -> None:
    d = _strategy_dir(domain)
    os.makedirs(d, exist_ok=True)
    with open(_strategy_path(domain), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_page_md(domain: str, slug: str) -> str:
    path = os.path.join(_pages_dir(domain), f'{slug}.md')
    if not os.path.exists(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


def _save_page_md(domain: str, slug: str, content: str) -> None:
    d = _pages_dir(domain)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f'{slug}.md'), 'w', encoding='utf-8') as f:
        f.write(content)


def get_url_strategy(page_url: str):
    """
    Given a full page URL, return its keyword strategy context or None.
    Merges site-level (brand, global_keywords) with page-level (primary_keyword etc.).
    Called by alt_text.py to inject context into AI prompts.
    """
    if not page_url:
        return None
    domain = _domain_from_url(page_url)
    if not domain:
        return None
    strategy = load_strategy(domain)
    if not strategy:
        return None

    try:
        path = urlparse(page_url).path or '/'
    except Exception:
        path = '/'

    pages = strategy.get('pages', {})
    # Try exact match, then trailing-slash variants
    page_data = (
        pages.get(path)
        or pages.get(path.rstrip('/') + '/')
        or pages.get(path.rstrip('/'))
        or {}
    )

    result = {
        'brand': strategy.get('brand', ''),
        'brand_keywords': strategy.get('brand_keywords', []),
        'global_keywords': strategy.get('global_keywords', []),
        'primary_keyword': page_data.get('primary_keyword', ''),
        'secondary_keywords': page_data.get('secondary_keywords', []),
        'intent': page_data.get('intent', ''),
        'notes': page_data.get('_notes', ''),
    }

    # Load linked MD file if present
    md_file = page_data.get('md_file', '')
    if md_file:
        slug = md_file.replace('.md', '')
        result['notes'] = _load_page_md(domain, slug) or result['notes']

    # Only return if there is something actionable
    has_content = (
        result['primary_keyword']
        or result['global_keywords']
        or result['brand']
    )
    return result if has_content else None


# ── Routes ────────────────────────────────────────────────────

@strategy_bp.route('/keyword-strategy', methods=['GET'])
def get_strategy():
    site = (request.args.get('site') or '').strip()
    if not site:
        return jsonify({'error': 'site is required'}), 400
    domain = _domain_from_url(site) or site
    data = load_strategy(domain)
    return jsonify({'exists': data is not None, 'strategy': data})


@strategy_bp.route('/keyword-strategy', methods=['POST'])
def save_strategy_route():
    body = request.get_json(silent=True) or {}
    site = (body.get('site') or '').strip()
    data = body.get('strategy')
    if not site or data is None:
        return jsonify({'error': 'site and strategy are required'}), 400
    if not isinstance(data, dict):
        return jsonify({'error': 'strategy must be a JSON object'}), 400
    domain = _domain_from_url(site) or site
    try:
        _save_strategy(domain, data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True})


@strategy_bp.route('/keyword-strategy/list', methods=['GET'])
def list_strategies():
    if not os.path.exists(_STRATEGIES_DIR):
        return jsonify({'strategies': []})
    strategies = []
    try:
        for entry in os.scandir(_STRATEGIES_DIR):
            if not entry.is_dir():
                continue
            strat_path = os.path.join(entry.path, 'strategy.json')
            if not os.path.exists(strat_path):
                continue
            try:
                modified = os.stat(strat_path).st_mtime
                with open(strat_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                continue
            strategies.append({
                'domain': entry.name,
                'brand': data.get('brand', ''),
                'page_count': len(data.get('pages', {})),
                'modified': modified,
            })
    except Exception:
        pass
    strategies.sort(key=lambda x: x.get('modified', 0), reverse=True)
    return jsonify({'strategies': strategies})


@strategy_bp.route('/keyword-strategy', methods=['DELETE'])
def delete_strategy_route():
    body = request.get_json(silent=True) or {}
    site = (body.get('site') or '').strip()
    if not site:
        return jsonify({'error': 'site is required'}), 400
    domain = _domain_from_url(site) or site
    d = _strategy_dir(domain)
    if not os.path.exists(d):
        return jsonify({'error': 'not found'}), 404
    try:
        import shutil
        shutil.rmtree(d)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True})


@strategy_bp.route('/keyword-strategy/generate-draft', methods=['POST'])
def generate_draft():
    """Use AI to draft keyword strategies from crawl data."""
    body = request.get_json(silent=True) or {}
    site_url = (body.get('site_url') or '').strip()
    pages = body.get('pages', [])

    if not site_url:
        return jsonify({'error': 'site_url is required'}), 400
    if not pages:
        return jsonify({'error': 'pages is required'}), 400

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'OPENAI_API_KEY not set — cannot auto-draft'}), 503

    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    domain = _domain_from_url(site_url) or site_url

    # Load existing strategy; preserve any manually edited entries
    existing = load_strategy(domain) or {
        'site': domain,
        'brand': '',
        'brand_keywords': [],
        'global_keywords': [],
        'pages': {}
    }

    # ── Step 1: site analysis (one call on homepage data) ─────
    # Find homepage or use first page as proxy
    homepage = next(
        (p for p in pages if (p.get('path') or '/') in ('/', '')),
        pages[0] if pages else None
    )
    site_context = ''
    if homepage:
        analysis_lines = [f'Site URL: {site_url}']
        if homepage.get('title'):
            analysis_lines.append(f'Title: {homepage["title"]}')
        if homepage.get('h1'):
            analysis_lines.append(f'H1: {homepage["h1"]}')
        if homepage.get('body'):
            analysis_lines.append(f'Body excerpt: {homepage["body"][:600]}')
        try:
            analysis = client.chat.completions.create(
                model='gpt-4o-mini',
                response_format={'type': 'json_object'},
                max_tokens=400,
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'Analyze this website homepage and identify the business. '
                            'Return JSON with these fields:\n'
                            '- business_type: short description of what the business does\n'
                            '- location: city/region name, or "national" if not location-specific\n'
                            '- target_customer: who the business serves\n'
                            '- brand_name: the official business name\n'
                            '- brand_keywords: array of 2-4 alternative names/abbreviations people search for this brand (e.g. shortened name, acronym, common misspelling). Empty array if no clear alternatives.\n'
                            '- global_keywords: array of 3-5 industry/service themes that appear across the whole site — phrases real people search, not internal jargon\n'
                            'Example: {"business_type": "...", "location": "...", "target_customer": "...", '
                            '"brand_name": "...", "brand_keywords": ["...", "..."], "global_keywords": ["...", "...", "..."]}'
                        )
                    },
                    {'role': 'user', 'content': '\n'.join(analysis_lines)}
                ]
            )
            site_info = json.loads(analysis.choices[0].message.content)
            parts = []
            if site_info.get('business_type'):
                parts.append(site_info['business_type'])
            if site_info.get('location') and site_info['location'].lower() != 'national':
                parts.append(f'based in {site_info["location"]}')
            if site_info.get('target_customer'):
                parts.append(f'targeting {site_info["target_customer"]}')
            site_context = ', '.join(parts)
            if site_info.get('brand_name') and not existing.get('brand'):
                existing['brand'] = site_info['brand_name']
            if site_info.get('brand_keywords'):
                existing['brand_keywords'] = site_info['brand_keywords']
            if site_info.get('global_keywords'):
                existing['global_keywords'] = site_info['global_keywords']
        except Exception:
            pass  # non-fatal — batches proceed without site context

    _SYSTEM_PROMPT = (
        'You are an experienced SEO strategist. For each page, suggest the single best '
        'target keyword — a phrase real people actually type into Google, not a description '
        'of the page.\n\n'
        'Rules:\n'
        '- Use the business context to inform local/industry-specific keywords\n'
        '- Include a location qualifier for service and product pages if the business is local or regional\n'
        '- Primary keyword must match the page\'s actual content — derive it from the title, H1, H2s, and body\n'
        '- Be specific enough to be realistic, not so broad it\'s dominated by national competitors\n'
        '- Never invent phrases people don\'t actually search\n'
        '- Use navigational intent for contact, about, and team pages\n'
        'Return only valid JSON matching the requested schema.'
    )

    # ── Step 2: batch keyword drafting (parallel) ─────────────
    from concurrent.futures import ThreadPoolExecutor, as_completed

    BATCH_SIZE = 20
    batches = [pages[i:i + BATCH_SIZE] for i in range(0, len(pages), BATCH_SIZE)]

    def _build_prompt(batch):
        lines = []
        for p in batch:
            path = p.get('path') or (urlparse(p.get('url', '')).path) or '/'
            parts = [f'URL: {path}']
            if p.get('title'):
                parts.append(f'Title: {p["title"]}')
            if p.get('h1') and p.get('h1') != p.get('title'):
                parts.append(f'H1: {p["h1"]}')
            if p.get('meta'):
                parts.append(f'Meta: {p["meta"][:120]}')
            h2s = p.get('h2s') or []
            if h2s:
                parts.append(f'H2s: {" / ".join(h2s[:3])}')
            if p.get('body'):
                parts.append(f'Content: {p["body"][:350]}')
            lines.append('\n  '.join(parts))
        return (
            f'Site: {site_url}\n'
            + (f'Business context: {site_context}\n' if site_context else '')
            + '\nFor each URL suggest:\n'
            '- primary_keyword: the single best phrase someone would Google to find this page\n'
            '- secondary_keywords: 2-3 natural variations\n'
            '- intent: informational / commercial / transactional / navigational\n\n'
            'Return JSON: {"pages": {"/path/": {"primary_keyword": "...", '
            '"secondary_keywords": ["..."], "intent": "..."}}}\n\n'
            'Pages:\n' + '\n\n'.join(lines)
        )

    def _run_batch(batch):
        completion = client.chat.completions.create(
            model='gpt-4o-mini',
            response_format={'type': 'json_object'},
            max_tokens=2500,
            messages=[
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': _build_prompt(batch)}
            ]
        )
        return json.loads(completion.choices[0].message.content).get('pages', {})

    all_generated = {}
    with ThreadPoolExecutor(max_workers=min(len(batches), 8)) as pool:
        futures = {pool.submit(_run_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                all_generated.update(future.result())
            except Exception as e:
                return jsonify({'error': f'AI generation failed: {str(e)}'}), 200

    # Merge: overwrite AI-generated pages with fresh output, never overwrite manually edited ones
    for path, data in all_generated.items():
        existing_page = existing['pages'].get(path, {})
        if existing_page.get('_source') == 'manual':
            continue  # user has edited this page — leave it alone
        data['_source'] = 'auto'
        existing['pages'][path] = data

    return jsonify({'ok': True, 'strategy': existing})
