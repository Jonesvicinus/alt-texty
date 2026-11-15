#!/usr/bin/env python3
"""osc: Open SEO Crawler from the command line.

Runs crawls and reads saved crawls through the crawler that is already
running (http://127.0.0.1:5002, or OSC_URL). Made for scripts, cron jobs,
CI checks and AI coding assistants that work in a terminal. Every command
takes --json for machine-readable output.

    python3 osc.py crawl https://example.com --max-pages 200
    python3 osc.py summary latest
    python3 osc.py pages latest --issue "Missing H1"
    python3 osc.py page latest https://example.com/about/
    python3 osc.py brief latest --out brief.md
    python3 osc.py compare <older crawl> <newer crawl>
    python3 osc.py bots https://example.com/ https://example.com/pricing/
    python3 osc.py robots example.com
    python3 osc.py export latest crawl.xlsx
    python3 osc.py list
    python3 osc.py mcp        (run as an MCP server for AI apps)
    python3 osc.py setup      (print the settings an AI app needs)

A crawl reference is "latest", a file name from `osc.py list`, or part of the
site name (the newest match wins).

Exit codes: 0 ok, 1 --fail-on threshold met, 2 error.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_tools as at  # noqa: E402

SEV_HEADINGS = (('errors', 'Errors'), ('warnings', 'Warnings'), ('info', 'Info'))


def _out(args, data, human):
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        human(data)


def _print_summary(s):
    print(f"{s['site']}  ({s['crawl']})")
    print(f"  pages {s['pages']}, indexable 200s {s['indexable_200']}, max depth {s['max_depth']}, "
          f"avg response {s['avg_response_seconds']} s")
    print('  status codes: ' + ', '.join(f'{k} x{v}' for k, v in sorted(s['status_codes'].items())))
    pw = s['pages_with']
    print(f"  pages with errors {pw['errors']}, warnings {pw['warnings']}, info {pw['info']}")
    for key, heading in SEV_HEADINGS:
        items = s.get(key) or []
        if not items:
            continue
        print(f'\n{heading}')
        for it in items:
            print(f"  {it['pages']:>5}  {it['issue']}")
    dup = {k: v for k, v in (s.get('duplicates') or {}).items() if v}
    if dup:
        print('\nDuplicates: ' + ', '.join(f"{k.replace('duplicate_', '')} {v} groups" for k, v in dup.items()))
    if s.get('redirect_chains'):
        print(f"Redirect chains: {s['redirect_chains']}")
    if s.get('sitemap'):
        sm = s['sitemap']
        counts = ', '.join(f'{k} {v}' for k, v in sm['counts'].items() if v)
        print(f"Sitemap: {', '.join(sm['found']) or 'none'}" + (f' ({counts})' if counts else ''))
    if s.get('near_duplicate_pairs'):
        print(f"Near-duplicate pairs: {s['near_duplicate_pairs']}")
    if s.get('bot_check'):
        b = s['bot_check']
        flagged = len(b.get('page_issues') or {})
        print(f"Bot readability: {flagged} of {b['sample_pages']} sample pages flagged")


def _exit_for(summary, fail_on):
    if fail_on == 'error' and summary['pages_with']['errors']:
        return 1
    if fail_on == 'warn' and (summary['pages_with']['errors'] or summary['pages_with']['warnings']):
        return 1
    return 0


def cmd_crawl(args):
    opts = {'url': args.url, 'max_pages': args.max_pages, 'max_depth': args.max_depth,
            'max_workers': args.workers, 'crawl_delay': args.delay, 'render_js': args.render_js,
            'compare_no_js': args.compare_no_js, 'ignore_robots': args.ignore_robots,
            'ignore_noindex': args.ignore_noindex, 'include_patterns': args.include,
            'exclude_patterns': args.exclude, 'user_agent': args.user_agent,
            'solve_challenges': not args.no_solve_challenges, 'sitemap': not args.no_sitemap,
            'near_dup': not args.no_near_dup, 'bot_check': not args.no_bot_check}
    quiet = args.quiet or args.json
    state = {'last': ''}

    def progress(kind, msg):
        if quiet:
            return
        if kind == 'page':
            n = len(job_rows())
            if n % 25 == 0:
                print(f'  {n} pages...', file=sys.stderr, flush=True)
        elif kind == 'state':
            pass
        elif msg and msg != state['last']:
            state['last'] = msg
            print(f'  {msg}', file=sys.stderr, flush=True)

    holder = {}

    def job_rows():
        j = holder.get('job')
        return j.rows if j else []

    at.app_version()
    job = at.CrawlJob(opts, on_event=progress)
    holder['job'] = job
    if not quiet:
        print(f'Crawling {job.body["url"]} (cap {job.opts["max_pages"]} pages)...', file=sys.stderr, flush=True)
    try:
        job.run()
    except KeyboardInterrupt:
        job.stop()
        print('Stopping; saving what was fetched...', file=sys.stderr, flush=True)
    status = job.status()
    if job.state == 'error':
        raise at.ToolError(job.error or 'crawl failed')
    summary = at.crawl_summary(job.saved_file)
    if args.json:
        print(json.dumps({'status': status, 'summary': summary}, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Saved as \"{job.saved_name}\" ({status['elapsed_seconds']} s)\n")
        _print_summary(summary)
    return _exit_for(summary, args.fail_on)


def cmd_summary(args):
    s = at.crawl_summary(args.crawl, args.sample)
    _out(args, s, _print_summary)
    return _exit_for(s, args.fail_on)


def cmd_pages(args):
    res = at.get_pages(args.crawl, issue=args.issue, severity=args.severity, status=args.status,
                       url_contains=args.url_contains,
                       indexable=None if args.indexable is None else args.indexable == 'yes',
                       fields=args.fields, limit=args.limit, offset=args.offset, sort=args.sort)

    def human(r):
        print(f"{r['matched']} pages match (showing {r['returned']} from {r['offset']})")
        for p in r['pages']:
            issues = p.get('issues') or []
            print(f"  {p.get('status_code', '')}  {p.get('url', '')}" + (f'  [{len(issues)} issues]' if issues else ''))
    _out(args, res, human)
    return 0


def cmd_page(args):
    res = at.get_page(args.crawl, args.url)

    def human(r):
        p = r['page']
        for k in ('url', 'status_code', 'title', 'meta_description', 'h1', 'canonical', 'word_count', 'depth',
                  'indexable', 'response_time'):
            print(f'  {k}: {p.get(k)}')
        print('  issues:')
        for i in p.get('issues') or []:
            print(f'    - {i}')
        print(f"  inlinks: {r['inlinks_total']}")
        for link in r['inlinks'][:20]:
            print(f"    <- {link.get('source')}  \"{link.get('anchor', '')}\"")
    _out(args, res, human)
    return 0


def cmd_brief(args):
    text = at.get_brief(args.crawl, page_table=not args.no_pages, page_limit=args.page_limit)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'Wrote {args.out} ({len(text)} characters)', file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_list(args):
    res = at.list_crawls(args.site, args.limit)

    def human(r):
        for c in r['crawls']:
            print(f"  {c['saved_at']}  {c['pages']:>6} pages  {c['crawl']}")
        if r['total'] > len(r['crawls']):
            print(f"  ... {r['total'] - len(r['crawls'])} more (--limit)")
    _out(args, res, human)
    return 0


def cmd_compare(args):
    res = at.compare_crawls(args.before, args.after)
    _out(args, res, lambda r: print(json.dumps(r, indent=2, ensure_ascii=False, default=str)))
    return 0


def cmd_bots(args):
    res = at.bot_check(args.urls, delay=args.delay, render=not args.no_render)

    def human(r):
        print(f"{r['sample_pages']} pages checked")
        for name, v in r['by_crawler'].items():
            counts = ', '.join(f'{k} {n}' for k, n in (v.get('counts') or {}).items())
            print(f"  {name}: {counts}")
        for u, found in (r.get('page_issues') or {}).items():
            print(f'  {u}')
            for i in found:
                print(f'    - {i}')
        print(f"\n{r['caveat']}")
    _out(args, res, human)
    return 0


def cmd_robots(args):
    res = at.check_robots(args.url)
    _out(args, res, lambda r: print(json.dumps(r, indent=2, ensure_ascii=False, default=str)))
    return 0


def cmd_cms(args):
    res = at.detect_cms(args.url)
    _out(args, res, lambda r: print(json.dumps(r, indent=2, ensure_ascii=False, default=str)))
    return 0


def cmd_export(args):
    res = at.export_xlsx(args.crawl, args.path)
    _out(args, res, lambda r: print(f"Wrote {r['written']} ({r['bytes']} bytes)"))
    return 0


def cmd_mcp(args):
    import mcp_server
    mcp_server.main()
    return 0


def cmd_setup(args):
    info = at.mcp_setup(at.base_url())
    py, server = info['command'], info['args'][0]
    block = info['config']
    if args.json:
        print(json.dumps(block, indent=2))
        return 0
    env = ''.join(f'{k}={v} ' for k, v in info['env'].items())
    print('Connect an AI app to Open SEO Crawler over MCP (stdio). It needs this command:\n')
    print(f'  {env}{py} {server}\n')
    print('Apps that read an mcpServers JSON block:\n')
    print(json.dumps(block, indent=2))
    print('\nApps with an "mcp add" command in the terminal:\n')
    print(f'  <app> mcp add open-seo-crawler -- {py} {server}\n')
    print('The README has the exact steps per app. The crawler itself must be running '
          f'({at.base_url()}).')
    try:
        v = at.app_version()
        print(f"Crawler is running: {v.get('version') or v.get('sha') or 'ok'}")
    except at.ToolError as e:
        print(f'Crawler is NOT running right now: {e}')
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog='osc.py', description='Open SEO Crawler from the command line.')
    p.add_argument('--json', action='store_true', help='machine-readable output')
    sub = p.add_subparsers(dest='cmd', required=True)

    def common(sp):
        sp.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help='machine-readable output')
        return sp

    c = common(sub.add_parser('crawl', help='crawl a site, save it, print the summary'))
    c.add_argument('url')
    c.add_argument('--max-pages', type=int, help='page cap (default 1500)')
    c.add_argument('--max-depth', type=int, help='click depth (default 10)')
    c.add_argument('--workers', type=int, help='parallel fetches (default 5)')
    c.add_argument('--delay', type=float, help='seconds between requests per host (default 0.4)')
    c.add_argument('--render-js', action='store_true', help='render pages with a headless browser')
    c.add_argument('--compare-no-js', action='store_true', help='with --render-js, report JS-only content')
    c.add_argument('--ignore-robots', action='store_true', help='crawl what robots.txt disallows')
    c.add_argument('--ignore-noindex', action='store_true', help='follow links on noindex pages')
    c.add_argument('--include', help='only these URL patterns, one per line or comma-separated')
    c.add_argument('--exclude', help='skip these URL patterns, one per line or comma-separated')
    c.add_argument('--user-agent', help='chrome-mobile, googlebot, googlebot-mobile, bingbot, firefox, safari or a full string')
    c.add_argument('--no-solve-challenges', action='store_true', help='never open a browser window for a bot challenge')
    c.add_argument('--no-sitemap', action='store_true', help='skip the sitemap comparison')
    c.add_argument('--no-near-dup', action='store_true', help='skip near-duplicate detection')
    c.add_argument('--no-bot-check', action='store_true', help='skip the crawler readability check')
    c.add_argument('--fail-on', choices=['never', 'error', 'warn'], default='never',
                   help='exit 1 if any page has an error (or a warning)')
    c.add_argument('--quiet', action='store_true', help='no progress lines')
    c.set_defaults(func=cmd_crawl)

    s = common(sub.add_parser('summary', help='issue counts for a saved crawl'))
    s.add_argument('crawl', nargs='?', default='latest')
    s.add_argument('--sample', type=int, default=5, help='sample URLs per issue in --json output')
    s.add_argument('--fail-on', choices=['never', 'error', 'warn'], default='never')
    s.set_defaults(func=cmd_summary)

    pg = common(sub.add_parser('pages', help='filter the pages of a saved crawl'))
    pg.add_argument('crawl', nargs='?', default='latest')
    pg.add_argument('--issue')
    pg.add_argument('--severity', choices=['error', 'warn', 'info'])
    pg.add_argument('--status', help='404, 3xx, ...')
    pg.add_argument('--url-contains')
    pg.add_argument('--indexable', choices=['yes', 'no'])
    pg.add_argument('--fields', help='comma-separated, or "all"')
    pg.add_argument('--limit', type=int, default=50)
    pg.add_argument('--offset', type=int, default=0)
    pg.add_argument('--sort', default='issues', choices=['issues', 'url', 'depth', 'word_count', 'response_time',
                                                         'title_len', 'meta_len'])
    pg.set_defaults(func=cmd_pages)

    one = common(sub.add_parser('page', help='one URL in detail, with inlinks'))
    one.add_argument('crawl')
    one.add_argument('url')
    one.set_defaults(func=cmd_page)

    b = common(sub.add_parser('brief', help='the Markdown AI brief for a saved crawl'))
    b.add_argument('crawl', nargs='?', default='latest')
    b.add_argument('--no-pages', action='store_true', help='leave out the page table')
    b.add_argument('--page-limit', type=int, default=200)
    b.add_argument('--out', help='write to this file instead of the screen')
    b.set_defaults(func=cmd_brief)

    ls = common(sub.add_parser('list', help='saved crawls, newest first'))
    ls.add_argument('--site')
    ls.add_argument('--limit', type=int, default=20)
    ls.set_defaults(func=cmd_list)

    cp = common(sub.add_parser('compare', help='diff two saved crawls'))
    cp.add_argument('before')
    cp.add_argument('after')
    cp.set_defaults(func=cmd_compare)

    bt = common(sub.add_parser('bots', help='AI and search crawler readability for some URLs'))
    bt.add_argument('urls', nargs='+')
    bt.add_argument('--delay', type=float, default=0.4)
    bt.add_argument('--no-render', action='store_true', help='skip the JS-rendered browser baseline')
    bt.set_defaults(func=cmd_bots)

    rb = common(sub.add_parser('robots', help="a site's robots.txt, parsed"))
    rb.add_argument('url')
    rb.set_defaults(func=cmd_robots)

    cm = common(sub.add_parser('cms', help='detect the CMS behind a URL'))
    cm.add_argument('url')
    cm.set_defaults(func=cmd_cms)

    ex = common(sub.add_parser('export', help='write a saved crawl to .xlsx'))
    ex.add_argument('crawl')
    ex.add_argument('path')
    ex.set_defaults(func=cmd_export)

    sub.add_parser('mcp', help='run as an MCP server on stdin/stdout').set_defaults(func=cmd_mcp)
    common(sub.add_parser('setup', help='print the settings an AI app needs')).set_defaults(func=cmd_setup)
    return p


def _split_patterns(v):
    if not v:
        return v
    return '\n'.join(x.strip() for x in v.replace(',', '\n').splitlines() if x.strip())


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not hasattr(args, 'json'):
        args.json = False
    for k in ('include', 'exclude'):
        if hasattr(args, k):
            setattr(args, k, _split_patterns(getattr(args, k)))
    try:
        return args.func(args) or 0
    except at.ToolError as e:
        print(f'Error: {e}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
