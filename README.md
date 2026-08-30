# Personal RSS feeds for Feedly

A small GitHub Actions + GitHub Pages project that turns selected websites into static RSS 2.0 feeds.

## Architecture

website -> GitHub Actions -> Python scraper -> docs/feeds/*.xml -> GitHub Pages -> Feedly

No VPS, database, or paid hosting is required.

## Setup

1. Create a **public** GitHub repository.
2. Copy this repository into it.
3. In GitHub: Settings -> Pages -> Source: **GitHub Actions**.
4. Actions -> Update RSS feeds -> Run workflow.
5. Add these URLs to Feedly:

- `https://YOUR-USER.github.io/YOUR-REPO/feeds/mzv.xml`
- `https://YOUR-USER.github.io/YOUR-REPO/feeds/tmbk.xml`
- `https://YOUR-USER.github.io/YOUR-REPO/feeds/skalni-mlyn.xml`
- `https://YOUR-USER.github.io/YOUR-REPO/feeds/svet-energie.xml`

The workflow runs every 30 minutes.

## Important

This first version deliberately uses per-site parsers. That is more reliable than trying to force four unrelated sites through one selector configuration. If a site's HTML changes, only its parser needs changing.

The feed files are static. GitHub Pages only serves the XML; GitHub Actions does the fetching/parsing.
