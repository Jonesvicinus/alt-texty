#!/usr/bin/env python3
"""Build the MCP bundle (.mcpb) and keep server.json in step with it.

    python3 mcpb/build.py

Writes dist/open-seo-crawler-mcp.mcpb: manifest.json, icon.png, the MCP
server and agent_tools.py, and the licence. The bundle is the file a desktop
AI app installs in one click, and the file the MCP Registry listing points
at. It then writes the bundle's SHA-256 and the release URL for this version
into server.json, because the registry entry has to name the exact file.

The version comes from agent_tools.VERSION; manifest.json must say the same
or the build stops. Upload the bundle to a GitHub release tagged
mcp-v<version> before publishing server.json to the registry.
"""
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import agent_tools  # noqa: E402

REPO_URL = 'https://github.com/puneetindersingh/open-seo-crawler'
BUNDLE = 'open-seo-crawler-mcp.mcpb'
# A fixed timestamp keeps the zip, and so its hash, the same for the same files.
STAMP = (2026, 1, 1, 0, 0, 0)

FILES = [
    (os.path.join(HERE, 'manifest.json'), 'manifest.json'),
    (os.path.join(HERE, 'icon.png'), 'icon.png'),
    (os.path.join(REPO, 'mcp_server.py'), 'server/mcp_server.py'),
    (os.path.join(REPO, 'agent_tools.py'), 'server/agent_tools.py'),
    (os.path.join(REPO, 'LICENSE'), 'LICENSE'),
]


def release_url(version):
    return f'{REPO_URL}/releases/download/mcp-v{version}/{BUNDLE}'


def build(out_dir=None):
    version = agent_tools.VERSION
    manifest = json.load(open(os.path.join(HERE, 'manifest.json'), encoding='utf-8'))
    if manifest.get('version') != version:
        raise SystemExit(f'manifest.json says {manifest.get("version")}, agent_tools.VERSION says {version}')
    out_dir = out_dir or os.path.join(REPO, 'dist')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, BUNDLE)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for src, arc in FILES:
            info = zipfile.ZipInfo(arc, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(src, 'rb') as f:
                z.writestr(info, f.read())
    sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    return path, sha, version


def update_server_json(sha, version):
    sj_path = os.path.join(REPO, 'server.json')
    sj = json.load(open(sj_path, encoding='utf-8'))
    sj['version'] = version
    for pkg in sj.get('packages') or []:
        if pkg.get('registryType') == 'mcpb':
            pkg['identifier'] = release_url(version)
            pkg['version'] = version
            pkg['fileSha256'] = sha
    with open(sj_path, 'w', encoding='utf-8') as f:
        json.dump(sj, f, indent=2, ensure_ascii=False)
        f.write('\n')
    return sj_path


if __name__ == '__main__':
    bundle, digest, ver = build()
    sj = update_server_json(digest, ver)
    print(f'{bundle}\n  version {ver}\n  sha256  {digest}\n  release {release_url(ver)}\n  updated {sj}')
