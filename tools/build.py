#!/usr/bin/env python3
"""Build the Flovatar blog (blog.flovatar.com) into dist/: a static site for any object-storage bucket or static host.

  posts/*.md            ──►  dist/<slug>/index.html   (same paths as the original Medium blog: /<slug>)
  images/<post>/*       ──►  dist/images/…            (self-hosted post images)
  web/                  ──►  dist/ (favicons, logo) + dist/static/<name>.<hash>.css|js (cache: 1 year, immutable)
  dist-private/fonts/*.woff2 (licensed, gitignored) ──► dist/fonts/ + dist/static/<name>.<hash>.woff2
  + dist/feed.xml (RSS 2.0; also published at /feed), sitemap.xml, robots.txt, 404.html, og/*.jpg (1200x630),
    redirect pages for the old Medium extras (/about, /archive, /latest, /tagged/*, /p/<id>, …).

Every page is <dir>/index.html. Buckets have no directory index, so scripts/publish.sh also uploads each page at the
extension-less key (<slug>) and a CDN rewrite maps `…/` to `…/index.html`.
Usage: .venv/bin/python tools/build.py [--out DIR]
"""
import datetime as dt
import glob
import hashlib
import html
import json
import os
import re
import shutil
import sys
from email.utils import format_datetime
from urllib.parse import quote

import markdown
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 'https://blog.flovatar.com'
SITE_NAME = 'Flovatar Blog'
SITE_DESC = ('News, drops and deep dives from Flovatar: the collaborative NFT project on Flow where collectors '
             'build their own characters.')
FONTS = os.path.join(ROOT, 'dist-private', 'fonts')
WEB = os.path.join(ROOT, 'web')
# Medium pages that no longer exist: static redirect pages (meta refresh + canonical) to the blog home
REDIRECTS_HOME = ['about', 'archive', 'latest', 'list/predefined:6704d63431a0:READING_LIST']

T = {  # UI strings per language
    'en': {'min': 'min read', 'read_other': 'Leer en español', 'prev': 'Older', 'next': 'Newer', 'home': 'All posts'},
    'es': {'min': 'min de lectura', 'read_other': 'Read in English', 'prev': 'Anterior', 'next': 'Siguiente', 'home': 'Todas las entradas'},
}
MONTHS_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def esc(s):
    return html.escape(s or '', quote=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def hashed(data, name):
    h = hashlib.sha256(data).hexdigest()[:10]
    stem, ext = os.path.splitext(name)
    return f'{stem}.{h}{ext}'


def asset_dir(slug):
    return re.sub(r'[^a-z0-9-]', '', slug.replace('ð', 'd').replace('Ð', 'd').lower())


def parse_post(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'---\n(.*?)\n---\n\n?(.*)', raw, re.S)
    meta = {}
    for line in m.group(1).splitlines():
        k, v = line.split(':', 1)
        meta[k.strip()] = json.loads(v.strip())
    meta['body_md'] = m.group(2)
    meta['dt'] = dt.datetime.strptime(meta['date'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    meta['udt'] = dt.datetime.strptime(meta['updated'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    return meta


def nice_date(d, lang):
    if lang == 'es':
        return f'{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}'
    return d.strftime('%b %-d, %Y')


def url_of(slug):
    return f'{BASE}/{quote(slug)}'


def norm(s):
    """Comparable form of a heading/title: no markdown, punctuation, case or extra spaces."""
    s = re.sub(r'\{#[^}]*\}\s*$', '', s or '')
    s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)
    s = re.sub(r'[^\w\s]', ' ', s.lower())
    return ' '.join(s.split())


def strip_title_headings(p):
    """Medium repeats the post title/subtitle as the first heading(s) of the body; the page header shows them already.
    Drop leading body headings (before any other content, section breaks aside) that duplicate them."""
    dup = {norm(p['title']), norm(p['subtitle'])} - {''}
    blocks = p['body_md'].split('\n\n')
    out, leading = [], True
    for b in blocks:
        st = b.strip()
        if leading and st and st != '* * *':
            m = re.match(r'#{1,6} (.*)$', st)
            if m and '\n' not in st and norm(m.group(1)) in dup:
                continue
            leading = False
        out.append(b)
    while out and out[0].strip() in ('', '* * *'):  # no section break right below the header
        out.pop(0)
    p['body_md'] = '\n\n'.join(out)


def show_subtitle(p):
    """Header subtitle only when it's a real subtitle: not Medium's truncated first paragraph (…), and not a section
    heading that also appears in the body."""
    sub = p['subtitle']
    if not sub or sub.endswith('…'):
        return False
    heads = {norm(h) for h in re.findall(r'^#{1,6} (.*)$', p['body_md'], re.M)}
    return norm(sub) not in heads


def render_md(text):
    md = markdown.Markdown(extensions=['fenced_code', 'sane_lists', 'attr_list'], output_format='html')
    out = md.convert(text)
    out = out.replace('<p>* * *</p>', '<hr>').replace('<hr />', '<hr>')
    out = re.sub(r'<p>(<figure[\s\S]*?</figure>)</p>', r'\1', out)
    return out


def og_image(src_rel, out_path):
    """1200x630 JPEG crop of a post cover (first frame for animations)."""
    im = Image.open(os.path.join(ROOT, src_rel))
    im.seek(0)
    im = im.convert('RGB')
    W, H = 1200, 630
    scale = max(W / im.width, H / im.height)
    im = im.resize((max(W, round(im.width * scale)), max(H, round(im.height * scale))), Image.LANCZOS)
    left, top = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((left, top, left + W, top + H))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    im.save(out_path, 'JPEG', quality=85, optimize=True, progressive=True)


def default_og(out_path):
    W, H = 1200, 630
    im = Image.new('RGB', (W, H), (24, 8, 48))
    px = im.load()
    for y in range(H):  # pink → night gradient, like the site header
        t = y / H
        c = (int(237 * (1 - t) ** 2 + 24 * (1 - (1 - t) ** 2)), int(43 * (1 - t) ** 2 + 8), int(235 * (1 - t) ** 2 + 48 * (1 - (1 - t) ** 2)))
        for x in range(W):
            px[x, y] = c
    logo = Image.open(os.path.join(WEB, 'assets', 'flovatar-logo-horizontal-white.png')).convert('RGBA')
    lw = 760
    logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
    im.paste(logo, ((W - lw) // 2, 150), logo)
    d = ImageDraw.Draw(im)
    ttf = os.path.join(FONTS, 'Staatliches-Regular.ttf')
    font = ImageFont.truetype(ttf, 120) if os.path.exists(ttf) else ImageFont.load_default()
    d.text((W // 2, 470), 'BLOG', font=font, fill=(255, 238, 80), anchor='mm')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    im.save(out_path, 'JPEG', quality=88, optimize=True, progressive=True)


HEAD = '''<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}">
{alternates}<link rel="alternate" type="application/rss+xml" title="Flovatar Blog" href="{base}/feed">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="Flovatar Blog">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_image}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="{locale}">
{extra_meta}<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@flovatar">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{og_image}">
<meta name="theme-color" content="#180830">
<link rel="icon" href="/favicon.png" type="image/png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preload" href="{font_head}" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{font_body}" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{css}">
<script type="application/ld+json">{ld}</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top">
  <a class="brand" href="/index.html" aria-label="Flovatar Blog home"><img src="/assets/flovatar-logo-horizontal-white.svg" alt="Flovatar" width="150" height="48"><span class="tag">Blog</span></a>
  <nav aria-label="Links">
    <a class="btn btn-outline hide-s" href="https://story.flovatar.com">Podcast</a>
    <a class="btn btn-outline hide-s" href="/feed" type="application/rss+xml">RSS</a>
    <a class="btn btn-yellow" href="https://flovatar.com">Flovatar.com</a>
  </nav>
</header>
'''

FOOT = '''<footer class="foot">
  <p>© Flovatar · <a href="https://flovatar.com">flovatar.com</a> · <a href="https://story.flovatar.com">A Dust Odyssey podcast</a> · <a href="https://x.com/flovatar">X</a> · <a href="https://discord.gg/flovatar">Discord</a> · <a href="/feed">RSS</a> · Built on <a href="https://flow.com">Flow</a></p>
</footer>
<script src="{js}" defer></script>
</body>
</html>
'''


def page(ctx, **kw):
    v = dict(ctx)
    v.update(kw)
    v.setdefault('alternates', '')
    v.setdefault('extra_meta', '')
    v.setdefault('og_type', 'website')
    v.setdefault('locale', 'en_US')
    v.setdefault('lang', 'en')
    v['og_title'] = v.get('og_title') or v['title']
    v['title'], v['desc'], v['og_title'] = esc(v['title']), esc(v['desc']), esc(v['og_title'])
    return HEAD.format(**v) + v['main'] + FOOT.format(js=v['js'])


def redirect_page(target_path, canonical):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Flovatar Blog</title>'
            f'<meta name="robots" content="noindex"><link rel="canonical" href="{esc(canonical)}">'
            f'<meta http-equiv="refresh" content="0; url={esc(target_path)}"></head>'
            f'<body><p>Moved to <a href="{esc(target_path)}">{esc(canonical)}</a>.</p></body></html>\n')


def cdata(s):
    return '<![CDATA[' + s.replace(']]>', ']]]]><![CDATA[>') + ']]>'


def absolutize(h, page_url=''):
    h = re.sub(r'href="#', f'href="{page_url}#', h)
    return re.sub(r'(src|href)="/(?!/)', rf'\1="{BASE}/', h)


def main():
    out = os.path.join(ROOT, 'dist')
    if '--out' in sys.argv:
        out = os.path.abspath(sys.argv[sys.argv.index('--out') + 1])
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, 'static'))

    # ---------- static: web/ + images/
    for name in os.listdir(WEB):
        if name in ('style.css', 'app.js'):
            continue
        s = os.path.join(WEB, name)
        (shutil.copytree if os.path.isdir(s) else shutil.copy)(s, os.path.join(out, name))
    shutil.copytree(os.path.join(ROOT, 'images'), os.path.join(out, 'images'))

    # ---------- fonts (licensed: never in git; copied in at build time)
    font_map = {}
    if os.path.isdir(FONTS):
        os.makedirs(os.path.join(out, 'fonts'), exist_ok=True)
        for f in sorted(os.listdir(FONTS)):
            if f.endswith('.woff2'):
                b = open(os.path.join(FONTS, f), 'rb').read()
                shutil.copy(os.path.join(FONTS, f), os.path.join(out, 'fonts', f))
                font_map[f] = hashed(b, f)
                open(os.path.join(out, 'static', font_map[f]), 'wb').write(b)
    else:
        print('WARNING: dist-private/fonts missing: the site will use fallback fonts')
    css = open(os.path.join(WEB, 'style.css'), encoding='utf-8').read()
    for f, hn in font_map.items():
        css = css.replace(f'url(/fonts/{f})', f'url(/static/{hn})')
    css_name = hashed(css.encode(), 'style.css')
    write(os.path.join(out, 'static', css_name), css)
    js = open(os.path.join(WEB, 'app.js'), encoding='utf-8').read()
    js_name = hashed(js.encode(), 'app.js')
    write(os.path.join(out, 'static', js_name), js)
    ctx = {'base': BASE, 'css': f'/static/{css_name}', 'js': f'/static/{js_name}',
           'font_head': f'/static/{font_map.get("korolev-condensed-bold.woff2", "")}',
           'font_body': f'/static/{font_map.get("roboto-condensed-latin.woff2", "")}'}

    # ---------- posts
    posts = sorted((parse_post(p) for p in glob.glob(os.path.join(ROOT, 'posts', '*.md'))), key=lambda p: p['dt'], reverse=True)
    by_slug = {p['slug']: p for p in posts}
    twins = {}
    for p in posts:
        if p.get('translation_of'):
            twins[p['slug']] = p['translation_of']
            twins[p['translation_of']] = p['slug']
    default_og(os.path.join(out, 'og', 'blog.jpg'))
    for p in posts:
        strip_title_headings(p)
        p['html'] = render_md(p['body_md'])
        if p.get('cover'):
            og_image(p['cover'], os.path.join(out, 'og', asset_dir(p['slug']) + '.jpg'))
            p['og'] = f'{BASE}/og/{asset_dir(p["slug"])}.jpg'
        else:
            p['og'] = f'{BASE}/og/blog.jpg'

    for i, p in enumerate(posts):
        lang = p.get('lang', 'en')
        tr = T[lang]
        canonical = url_of(p['slug'])
        alternates = ''
        twin = by_slug.get(twins.get(p['slug'], ''))
        if twin:
            for q in sorted([p, twin], key=lambda q: q.get('lang', 'en')):
                alternates += f'<link rel="alternate" hreflang="{q.get("lang", "en")}" href="{url_of(q["slug"])}">\n'
            en = p if lang == 'en' else twin
            alternates += f'<link rel="alternate" hreflang="x-default" href="{url_of(en["slug"])}">\n'
        tags_meta = ''.join(f'<meta property="article:tag" content="{esc(t["title"])}">\n' for t in p['tags'])
        extra = (f'<meta property="article:published_time" content="{p["date"]}">\n'
                 f'<meta property="article:modified_time" content="{p["updated"]}">\n{tags_meta}')
        ld = {
            '@context': 'https://schema.org', '@type': 'BlogPosting', 'headline': p['title'],
            'description': p['subtitle'], 'datePublished': p['date'], 'dateModified': p['updated'],
            'inLanguage': lang, 'image': p['og'], 'mainEntityOfPage': canonical,
            'author': {'@type': 'Organization', 'name': 'Flovatar', 'url': 'https://flovatar.com'},
            'publisher': {'@type': 'Organization', 'name': 'Flovatar', 'url': 'https://flovatar.com',
                          'logo': {'@type': 'ImageObject', 'url': f'{BASE}/apple-touch-icon.png'}},
        }
        if twin:
            ld['translationOfWork' if lang != 'en' else 'workTranslation'] = {'@id': url_of(twin['slug'])}
        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        pager = '<nav class="pager" aria-label="More posts">'
        if older:
            pager += f'<a class="prev" href="/{quote(older["slug"])}"><small>← {tr["prev"]}</small>{esc(older["title"])}</a>'
        if newer:
            pager += f'<a class="next" href="/{quote(newer["slug"])}"><small>{tr["next"]} →</small>{esc(newer["title"])}</a>'
        pager += '</nav>'
        translation = (f'<p class="translation"><a href="/{quote(twin["slug"])}" hreflang="{twin.get("lang", "en")}" '
                       f'lang="{twin.get("lang", "en")}">{tr["read_other"]} →</a></p>') if twin else ''
        tags = ('<ul class="tags">' + ''.join(f'<li>{esc(t["title"])}</li>' for t in p['tags']) + '</ul>') if p['tags'] else ''
        main = f'''<main id="main">
<article class="post">
  <header>
    <p class="eyebrow"><a href="/index.html" style="color:inherit;text-decoration:none">{T[lang]["home"]}</a></p>
    <h1>{esc(p["title"])}</h1>
    {f'<p class="subtitle">{esc(p["subtitle"])}</p>' if show_subtitle(p) else ''}
    <p class="meta"><time datetime="{p["date"]}">{nice_date(p["dt"], lang)}</time><span>· {p["reading_time"]} {tr["min"]}</span></p>
    {translation}
  </header>
  <div class="body">
{p["html"]}
  </div>
  {tags}
</article>
{pager}
</main>
'''
        write(os.path.join(out, p['slug'], 'index.html'), page(
            ctx, lang=lang, title=f'{p["title"]} | Flovatar Blog', og_title=p['title'], desc=p['subtitle'] or SITE_DESC,
            canonical=canonical, alternates=alternates, og_type='article', og_image=p['og'],
            locale='es_ES' if lang == 'es' else 'en_US', extra_meta=extra,
            ld=json.dumps(ld, ensure_ascii=False).replace('</', '<\\/'), main=main))

    # ---------- home
    cards = []
    for i, p in enumerate(posts):
        lang = p.get('lang', 'en')
        thumb = f'/og/{asset_dir(p["slug"])}.jpg' if p.get('cover') else '/og/blog.jpg'
        cards.append(f'''<li class="{'featured' if i == 0 else ''}"><article class="card"><a href="/{quote(p["slug"])}"{f' hreflang="{lang}"' if lang != 'en' else ''}>
  <div class="thumb"><img src="{thumb}" alt="" width="1200" height="630" {'' if i < 3 else 'loading="lazy" '}decoding="async"></div>
  <div class="txt"><p class="meta"><time datetime="{p["date"]}">{nice_date(p["dt"], "en")}</time>{' <span class="lang">ES</span>' if lang == 'es' else ''}</p>
  <h2{f' lang="{lang}"' if lang != 'en' else ''}>{esc(p["title"])}</h2>
  {f'<p{chr(32) + "lang=" + chr(34) + lang + chr(34) if lang != "en" else ""}>{esc(p["subtitle"])}</p>' if p["subtitle"] else ''}</div>
</a></article></li>''')
    main = f'''<main id="main">
<section class="hero">
  <p class="eyebrow">News from the Megaverse</p>
  <h1>Flovatar Blog</h1>
  <p class="lead">{esc(SITE_DESC)}</p>
</section>
<ul class="posts">
{chr(10).join(cards)}
</ul>
</main>
'''
    ld = {'@context': 'https://schema.org', '@type': 'Blog', 'name': SITE_NAME, 'url': BASE + '/', 'description': SITE_DESC,
          'publisher': {'@type': 'Organization', 'name': 'Flovatar', 'url': 'https://flovatar.com'},
          'blogPost': [{'@type': 'BlogPosting', 'headline': p['title'], 'url': url_of(p['slug']), 'datePublished': p['date']} for p in posts]}
    write(os.path.join(out, 'index.html'), page(
        ctx, title='Flovatar Blog', desc=SITE_DESC, canonical=BASE + '/', og_image=f'{BASE}/og/blog.jpg',
        ld=json.dumps(ld, ensure_ascii=False).replace('</', '<\\/'), main=main))

    # ---------- 404
    write(os.path.join(out, '404.html'), page(
        ctx, title='Not found | Flovatar Blog', desc=SITE_DESC, canonical=BASE + '/', og_image=f'{BASE}/og/blog.jpg', ld='{}',
        main='''<main id="main" class="hero" style="text-align:center"><img src="/assets/flovatar-mark-pink.svg" alt="" width="120" height="120">
<h1>Lost in the Megaverse</h1><p class="lead" style="margin:0 auto 24px">This page took a wrong turn on the Meta Highway.</p>
<p><a class="btn btn-yellow" href="/index.html">Back to the blog</a></p></main>
''').replace('<link rel="canonical" href="https://blog.flovatar.com/">\n', '<meta name="robots" content="noindex">\n'))

    # ---------- redirect pages for the old Medium extras
    redirects = {r: ('/', BASE + '/') for r in REDIRECTS_HOME}
    for p in posts:
        for t in p['tags']:
            redirects[f'tagged/{t["slug"]}'] = ('/', BASE + '/')
        redirects[f'p/{p["medium_id"]}'] = (f'/{quote(p["slug"])}', url_of(p['slug']))
    for path, (target, canonical) in redirects.items():
        write(os.path.join(out, path, 'index.html'), redirect_page(target, canonical))

    # ---------- RSS 2.0 (/feed and /feed.xml)
    last = max(p['udt'] for p in posts)
    items = []
    for p in posts:
        body = absolutize(p['html'], url_of(p['slug']))
        if p.get('cover'):
            body = f'<p><img src="{p["og"]}" alt="" width="1200" height="630"></p>' + body if 'figure' not in body[:400] else body
        cats = ''.join(f'\n      <category>{cdata(t["slug"])}</category>' for t in p['tags'])
        items.append(f'''    <item>
      <title>{cdata(p["title"])}</title>
      <link>{url_of(p["slug"])}</link>
      <guid isPermaLink="false">https://medium.com/p/{p["medium_id"]}</guid>{cats}
      <dc:creator>{cdata("Flovatar")}</dc:creator>
      <pubDate>{format_datetime(p["dt"], usegmt=True)}</pubDate>
      <atom:updated>{p["updated"].replace("Z", ".000Z")}</atom:updated>
      <description>{cdata(p["subtitle"] or p["title"])}</description>
      <content:encoded>{cdata(body)}</content:encoded>
    </item>''')
    feed = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Flovatar Blog</title>
    <description>{esc(SITE_DESC)}</description>
    <link>{BASE}/</link>
    <image>
      <url>{BASE}/apple-touch-icon.png</url>
      <title>Flovatar Blog</title>
      <link>{BASE}/</link>
    </image>
    <language>en</language>
    <generator>flovatar-blog</generator>
    <lastBuildDate>{format_datetime(last, usegmt=True)}</lastBuildDate>
    <atom:link href="{BASE}/feed" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
  </channel>
</rss>
'''
    write(os.path.join(out, 'feed.xml'), feed)

    # ---------- sitemap + robots
    urls = [f'  <url><loc>{BASE}/</loc><lastmod>{last.date().isoformat()}</lastmod></url>']
    for p in posts:
        alt = ''
        twin = by_slug.get(twins.get(p['slug'], ''))
        if twin:
            for q in (p, twin):
                alt += f'<xhtml:link rel="alternate" hreflang="{q.get("lang", "en")}" href="{url_of(q["slug"])}"/>'
        urls.append(f'  <url><loc>{url_of(p["slug"])}</loc><lastmod>{p["udt"].date().isoformat()}</lastmod>{alt}</url>')
    write(os.path.join(out, 'sitemap.xml'), '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
          + '\n'.join(urls) + '\n</urlset>\n')
    write(os.path.join(out, 'robots.txt'), f'# blog.flovatar.com: the Flovatar blog\nUser-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n')

    n = sum(len(f) for _, _, f in os.walk(out))
    print(f'built {out}: {len(posts)} posts, {len(redirects)} redirects, {n} files, fonts={len(font_map)}')


if __name__ == '__main__':
    main()
