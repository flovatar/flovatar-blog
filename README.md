# flovatar-blog

The Flovatar blog, published at **https://blog.flovatar.com** (a static site in object storage behind Cloudflare).
Every post keeps the URL it had on the original Medium blog (`https://blog.flovatar.com/<slug>`), and the RSS feed
stays at `/feed`.

| Path | What |
|---|---|
| `posts/<slug>.md` | One Markdown file per post (front matter: title, subtitle, date, updated, lang, tags, cover, Medium id). Images are HTML `<figure>`s so captions and sizes survive. Edit these. |
| `images/<post>/` | Self-hosted post images (WebP, or GIF where the animation is smaller as GIF). |
| `web/` | `style.css` (Flovatar palette and type, see https://brand.flovatar.com), `app.js` (click-to-load YouTube embeds), favicons, logos. |
| `tools/build.py` | Generator: posts + web → `dist/` (post pages, home, RSS feed, sitemap, robots, 404, 1200×630 OG images, redirect pages for retired Medium URLs). |
| `tools/import_medium.py` | One-time importer from Medium (kept for reproducibility; post list in `tools/medium_posts.json`). |
| `scripts/publish.sh` | Syncs `dist/` to an S3-compatible bucket with per-type Content-Type and Cache-Control, plus extension-less copies of every page and of the feed. Target from env vars or a gitignored `scripts/publish.local.env` (`BUCKET`, `S3_ENDPOINT`, optional `AWS_PROFILE`). |
| `dist/` | Build output (**gitignored**: it contains licensed webfonts). |
| `dist-private/fonts/` | Licensed `.woff2` webfonts (**gitignored**; kept by the Flovatar team, published to the site only). |

## Build & publish

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# put the webfonts in dist-private/fonts/ (same files as the brand guide); without them the site uses system fonts
.venv/bin/python tools/build.py                  # → dist/
python3 -m http.server -d dist 8000              # local preview: http://127.0.0.1:8000/index.html
BUCKET=… S3_ENDPOINT=… scripts/publish.sh         # or put them in scripts/publish.local.env (gitignored)
```

### Writing a new post

Add `posts/<slug>.md` with the same front matter as the existing posts (`slug` = file name = URL path, `date`/`updated`
in UTC ISO format, `lang`, `tags`, optional `cover` image path), put its images in `images/<slug>/`, build, publish.

### How URLs work

- Pages are built as `<path>/index.html`. The bucket has no directory index, so `publish.sh` also uploads each page
  at the extension-less key `<path>` (the original Medium URLs have no trailing slash) and a Cloudflare URL-rewrite
  rule maps paths ending in `/` to `index.html` (needed for `/`).
- `/feed` and `/feed.xml` serve the same RSS 2.0 feed (`application/rss+xml`). Item GUIDs are the original Medium
  GUIDs, so feed readers don't show old posts again.
- Retired Medium pages (`/about`, `/archive`, `/latest`, `/tagged/<tag>`, `/p/<id>`, …) are small redirect pages.
- The Spanish post is marked `lang="es"` and linked to its English original with `hreflang`.
- CSS, JS and fonts are content-hashed under `/static/` (cached for a year); HTML, feed and sitemap for 5 minutes,
  images for 1 day.

## Rules for editors

- **This repo is public:** never commit font files, design sources, secrets, personal data or internal details
  (storage locations, machine paths, server names, internal channels or documents, private business figures,
  unannounced plans). Credit art to "the Flovatar team"; never name individual artists.

## Licensing

- **Code** (`tools/`, `scripts/`, `web/*.css`, `web/*.js`): MIT, see `LICENSE`.
- **Content** (posts, images, logos, characters and art): © Flovatar, all rights reserved. Third-party marks belong to
  their owners. The commercial fonts are licensed to Flovatar and are not distributed in this repository.
