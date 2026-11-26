#!/usr/bin/env python3
"""Import the Flovatar posts from Medium into posts/*.md + images/ (one-time migration tool, kept for reproducibility).

Usage: .venv/bin/python tools/import_medium.py [--raw .cache/medium] [--refetch] [--force]

- Reads the post list from tools/medium_posts.json (Medium post id + uniqueSlug).
- Fetches each post's full body model from Medium's public GraphQL endpoint (needs curl_cffi: Medium blocks plain
  HTTP clients) and caches the JSON under --raw (gitignored).
- Converts the body to Markdown (headings, lists, quotes, code, bold/italic/links, section breaks); images become HTML
  <figure>s with captions, embeds become lite YouTube embeds or link cards.
- Downloads every image at full resolution from miro.medium.com into images/<uniqueSlug>/ (JPEG/PNG → WebP q85,
  max 2000 px wide, aspect kept; animated GIFs kept as GIF).
- Writes posts/<uniqueSlug>.md (front matter = JSON values, valid YAML). Existing posts are kept unless --force.
"""
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q = """query($id:ID!){post(id:$id){id title uniqueSlug firstPublishedAt latestPublishedAt mediumUrl readingTime
 previewImage{id originalWidth originalHeight} previewContent{subtitle} tags{id displayTitle normalizedTagSlug}
 content{bodyModel{sections{name startIndex} paragraphs{name type text href layout hasDropCap
  metadata{id originalWidth originalHeight alt} markups{type start end href anchorType}
  mixtapeMetadata{href thumbnailImageId} iframe{mediaResource{id href iframeSrc iframeWidth iframeHeight title}}}}}}}"""
MAX_W = 2000
# posts that are translations: lang + the uniqueSlug of the original
TRANSLATIONS = {
    'flovatar-presenta-el-pack-terrakit-y-la-preventa-exclusiva-de-terrenos-483b2d7c207':
        ('es', 'introducing-the-flovatar-terrakit-pack-and-the-exclusive-land-pre-sale-735e697f0563'),
}


def session():
    from curl_cffi import requests as cr  # imported lazily so the build never needs it
    return cr


def fetch_post(pid, raw_dir, refetch):
    p = os.path.join(raw_dir, pid + '.json')
    if os.path.exists(p) and not refetch:
        return json.load(open(p))
    r = session().post('https://medium.com/_/graphql', json={'query': Q, 'variables': {'id': pid}},
                       impersonate='chrome', timeout=30)
    d = r.json()['data']['post']
    json.dump(d, open(p, 'w'), indent=1, ensure_ascii=False)
    time.sleep(0.7)
    return d


# ---------------------------------------------------------------- inline markup
MD_ESC = re.compile(r'([\\`*_\[\]])')


def esc_md(s):
    s = MD_ESC.sub(r'\\\1', s)
    return s.replace('<', '&lt;').replace('>', '&gt;')


def inline(text, markups, html_mode=False):
    """Apply Medium markups (STRONG/EM/CODE/A) to text → Markdown (or HTML for captions/link cards)."""
    text = text or ''
    opens, closes = {}, {}
    for i, m in enumerate(markups or []):
        s, e = m['start'], m['end']
        # keep surrounding whitespace outside the markers (Markdown needs `**word**`, not `** word **`)
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s >= e:
            continue
        opens.setdefault(s, []).append((e, i, m))
        closes.setdefault(e, []).append((s, i, m))

    def tag(m, opening):
        t = m['type']
        if html_mode:
            name = {'STRONG': 'strong', 'EM': 'em', 'CODE': 'code', 'A': 'a'}.get(t)
            if not name:
                return ''
            if opening:
                return f'<a href="{escape(m["href"] or "")}">' if t == 'A' else f'<{name}>'
            return f'</{name}>'
        if t == 'STRONG':
            return '**'
        if t == 'EM':
            return '_'
        if t == 'CODE':
            return '`'
        if t == 'A':
            return '[' if opening else f']({(m["href"] or "").replace(" ", "%20").replace(")", "%29")})'
        return ''

    out, stack = [], []
    for pos in range(len(text) + 1):
        if pos in closes:
            # close in reverse order of opening (proper nesting)
            ending = {i for _, i, _ in closes[pos]}
            reopen = []
            while stack and ending:
                e, i, m = stack.pop()
                out.append(tag(m, False))
                if i in ending:
                    ending.discard(i)
                else:
                    reopen.append((e, i, m))
            for item in reversed(reopen):
                out.append(tag(item[2], True))
                stack.append(item)
        if pos in opens:
            for e, i, m in sorted(opens[pos], key=lambda x: -x[0]):  # longest first = outermost
                out.append(tag(m, True))
                stack.append((e, i, m))
        if pos < len(text):
            ch = text[pos]
            out.append(escape(ch) if html_mode else esc_md(ch))
    while stack:
        out.append(tag(stack.pop()[2], False))
    s = ''.join(out)
    if not html_mode:
        s = s.replace('\n', '  \n')  # Medium soft line breaks
    else:
        s = s.replace('\n', '<br>')
    return s


# ---------------------------------------------------------------- images
def asset_dir(slug):
    """ASCII-only image folder name (post slugs may contain letters like ð)."""
    return re.sub(r'[^a-z0-9-]', '', slug.replace('ð', 'd').replace('Ð', 'd').lower())


def save_image(img_id, slug, n):
    """Download a Medium image at full resolution; return (relative path, width, height)."""
    from PIL import Image
    slug = asset_dir(slug)
    d = os.path.join(ROOT, 'images', slug)
    os.makedirs(d, exist_ok=True)
    base = f'{n:02d}'
    for ext in ('.webp', '.gif', '.jpg', '.png'):
        p = os.path.join(d, base + ext)
        if os.path.exists(p):
            with Image.open(p) as im:
                return f'images/{slug}/{base}{ext}', im.width, im.height
    r = session().get(f'https://miro.medium.com/v2/{img_id}', impersonate='chrome', timeout=60)
    r.raise_for_status()
    data = r.content
    im = Image.open(io.BytesIO(data))
    animated = getattr(im, 'is_animated', False)
    if im.format == 'GIF' and animated:
        # animated WebP when it is clearly smaller (keeps frame timing + loop), else the original GIF
        b = io.BytesIO()
        im.save(b, 'WEBP', save_all=True, quality=80, method=4, minimize_size=True, loop=im.info.get('loop', 0))
        if len(b.getvalue()) < 0.7 * len(data):
            open(os.path.join(d, base + '.webp'), 'wb').write(b.getvalue())
            return f'images/{slug}/{base}.webp', im.width, im.height
        p = os.path.join(d, base + '.gif')
        open(p, 'wb').write(data)
        return f'images/{slug}/{base}.gif', im.width, im.height
    im.load()
    if im.mode in ('P', 'LA', 'PA'):
        im = im.convert('RGBA')
    elif im.mode not in ('RGB', 'RGBA'):
        im = im.convert('RGB')
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    p = os.path.join(d, base + '.webp')
    im.save(p, 'WEBP', quality=85, method=6)
    return f'images/{slug}/{base}.webp', im.width, im.height


def youtube_id(url):
    m = re.search(r'(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([\w-]{11})', url or '')
    return m.group(1) if m else None


def iframe_info(p):
    """Resolve a Medium IFRAME paragraph to its source URL + title (Medium proxies embeds via /media/<id>)."""
    r = (p.get('iframe') or {}).get('mediaResource') or {}
    src = r.get('href') or r.get('iframeSrc') or ''
    if not src and r.get('id'):
        try:
            h = session().get(f'https://medium.com/media/{r["id"]}', impersonate='chrome', timeout=30).text
            m = re.search(r'(?:src|href)="(https?://(?:www\.)?(?:youtube\.com|youtu\.be|twitter\.com|x\.com|player\.vimeo\.com)[^"]+)"', h)
            src = m.group(1).replace('&amp;', '&') if m else ''
        except Exception:  # noqa: BLE001
            src = ''
    return src, r.get('title') or ''


# ---------------------------------------------------------------- body
def convert(post):
    slug = post['uniqueSlug']
    body = post['content']['bodyModel']
    breaks = {s['startIndex'] for s in body['sections'] if s['startIndex'] > 0}
    paras = body['paragraphs']
    blocks, n_img, prev_type = [], 0, None
    # in-post links (#<paragraph name>) → keep the Medium paragraph name as the heading id
    targets = {m['href'][1:] for q in paras for m in q['markups'] if m['type'] == 'A' and (m['href'] or '').startswith('#')}
    first_title_skipped = False
    for idx, p in enumerate(paras):
        t, text = p['type'], p['text'] or ''
        if idx in breaks:
            blocks.append('* * *')
            prev_type = None
        # Medium repeats the post title as the first heading; the page template renders the title itself
        if not first_title_skipped and idx == 0 and t in ('H3', 'H2') and text.strip() == post['title'].strip():
            first_title_skipped = True
            continue
        md = None
        if t == 'P':
            md = inline(text, p['markups'])
            if re.match(r'(#|[-+] |\d+[.)] )', md):
                md = '\\' + md  # don't let a paragraph start turn into a heading/list
        elif t in ('H2', 'H3', 'H4'):
            md = ('## ' if t != 'H4' else '### ') + inline(text, [m for m in p['markups'] if m['type'] == 'A'])
            if p['name'] in targets:
                md += ' {#' + p['name'] + '}'
        elif t == 'ULI':
            md = '- ' + inline(text, p['markups'])
        elif t == 'OLI':
            md = '1. ' + inline(text, p['markups'])
        elif t == 'BQ':
            md = '> ' + inline(text, p['markups']).replace('  \n', '  \n> ')
        elif t == 'PQ':
            md = '<p class="pullquote">' + inline(text, p['markups'], html_mode=True) + '</p>'
        elif t == 'PRE':
            md = '```\n' + text + '\n```'
        elif t == 'IMG' and p.get('metadata'):
            n_img += 1
            rel, w, h = save_image(p['metadata']['id'], slug, n_img)
            alt = p['metadata'].get('alt') or text or ''
            cap = inline(text, p['markups'], html_mode=True)
            md = (f'<figure class="img"><img src="/{rel}" alt="{escape(alt)}" width="{w}" height="{h}" loading="lazy" '
                  f'decoding="async">' + (f'<figcaption>{cap}</figcaption>' if cap else '') + '</figure>')
        elif t == 'IFRAME':
            src, title = iframe_info(p)
            yid = youtube_id(src)
            if yid:
                md = (f'<figure class="embed"><a class="yt" href="https://www.youtube.com/watch?v={yid}" data-yt="{yid}">'
                      f'<img src="https://i.ytimg.com/vi/{yid}/hqdefault.jpg" alt="{escape(title or "Video")}" width="480" '
                      f'height="360" loading="lazy"><span class="yt-play" aria-hidden="true"></span>'
                      f'<span class="sr">Play video: {escape(title or "YouTube")}</span></a>'
                      + (f'<figcaption>{inline(text, p["markups"], html_mode=True)}</figcaption>' if text else '')
                      + '</figure>')
            elif src:
                md = f'<p class="embed-link"><a href="{escape(src)}">{escape(title or text or src)}</a></p>'
            else:
                print(f'  WARN unresolved embed in {slug}: {p.get("iframe")}')
        elif t == 'MIXTAPE_EMBED':
            href = (p.get('mixtapeMetadata') or {}).get('href') or ''
            md = f'<p class="card"><a href="{escape(href)}">{inline(text, [], html_mode=True)}</a></p>'
        else:
            md = inline(text, p['markups'])
            print(f'  WARN unknown paragraph type {t} in {slug}')
        if p['name'] in targets and t not in ('H2', 'H3', 'H4'):
            print(f'  WARN anchor target {p["name"]} is a {t} in {slug}')
        if md is None or md.strip() in ('', '##', '###', '-', '1.', '>'):
            continue
        # consecutive list items / quotes join without a blank line
        joiner = '\n' if (t == prev_type and t in ('ULI', 'OLI')) else '\n\n'
        if blocks and joiner == '\n':
            blocks[-1] += '\n' + md
        else:
            blocks.append(md)
        prev_type = t
    return '\n\n'.join(blocks) + '\n'


# public-repo rule: individual original artists are credited as a role, never by name
REDACT = [(re.compile(r'First and foremost to [A-Z][a-z]+ who'), 'First and foremost to our lead artist, who')]


def redact(md):
    for rx, sub in REDACT:
        md = rx.sub(sub, md)
    return md


def relink(md, ids):
    """Links to our own posts on any medium.com URL → the post's blog.flovatar.com URL."""
    rx = re.compile(r'https?://(?:[a-z0-9-]+\.)?medium\.com/(?:@[^/\s)"]+/|p/)?(?:[^/\s)"]*-)?([0-9a-f]{10,12})(?=[)\s"?#])')
    return rx.sub(lambda m: f'https://blog.flovatar.com/{ids[m.group(1)]}' if m.group(1) in ids else m.group(0), md)


def front_matter(meta):
    return '---\n' + ''.join(f'{k}: {json.dumps(v, ensure_ascii=False)}\n' for k, v in meta.items()) + '---\n\n'


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def main():
    args = sys.argv[1:]
    raw = os.path.join(ROOT, '.cache', 'medium')
    if '--raw' in args:
        raw = os.path.abspath(args[args.index('--raw') + 1])
    os.makedirs(raw, exist_ok=True)
    os.makedirs(os.path.join(ROOT, 'posts'), exist_ok=True)
    plist = json.load(open(os.path.join(ROOT, 'tools', 'medium_posts.json')))
    for item in plist:
        post = fetch_post(item['id'], raw, '--refetch' in args)
        slug = post['uniqueSlug']
        out = os.path.join(ROOT, 'posts', slug + '.md')
        if os.path.exists(out) and '--force' not in args:
            print('keep', slug)
            continue
        body = relink(redact(convert(post)), {i['id']: i['uniqueSlug'] for i in plist})
        lang, twin = TRANSLATIONS.get(slug, ('en', None))
        cover = None
        if post.get('previewImage') and post['previewImage'].get('id'):
            # reuse the body image with the same Medium id when there is one
            for i, p in enumerate([q for q in post['content']['bodyModel']['paragraphs'] if q['type'] == 'IMG' and q.get('metadata')], 1):
                if p['metadata']['id'] == post['previewImage']['id']:
                    cover = save_image(p['metadata']['id'], slug, i)[0]
                    break
            if not cover:
                cover = save_image(post['previewImage']['id'], slug, 99)[0]
        meta = {
            'title': post['title'],
            'subtitle': (post.get('previewContent') or {}).get('subtitle') or '',
            'slug': slug,
            'medium_id': post['id'],
            'date': iso(post['firstPublishedAt']),
            'updated': iso(post['latestPublishedAt']),
            'lang': lang,
            'translation_of': twin,
            'tags': [{'slug': t['normalizedTagSlug'] or t['id'], 'title': t['displayTitle']} for t in post['tags']],
            'cover': cover,
            'reading_time': round(post.get('readingTime') or 0),
        }
        open(out, 'w').write(front_matter(meta) + body)
        print('wrote', slug, len(body))


if __name__ == '__main__':
    main()
