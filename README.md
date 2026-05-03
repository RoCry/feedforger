# FeedForger 🔨⚡

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Forge multiple RSS/Atom feeds into organized JSON groups. Runs on GitHub Actions with scheduled updates and releases.

## Quick Start

1. Fork this repo
2. Edit `recipes/recipes.toml` with your feeds (supports `.toml` and `.opml`)
3. Enable GitHub Actions — uncomment the `schedule` trigger in `.github/workflows/main.yml`
4. Feeds are published as GitHub Releases

## Local Usage

```bash
uv sync --all-extras
uv run feedforger build          # build feeds from recipes/
uv run feedforger build --help   # see all options
uv run feedforger cleanup        # clean old cache entries
uv run feedforger report         # dump per-URL failure stats → cache/failure_report.json
```

## Pruning Dead URLs

Each scheduled run uploads a `failure-report` artifact (30-day retention)
with every URL's `continue_fail_count` and last error. Use it to find URLs
that have been failing for many consecutive runs and remove them from
`recipes/recipes.toml`:

```bash
gh run download -R <owner>/<repo> -n failure-report -D /tmp/ff
jq '.entries | map(select(.continue_fail_count >= 30))' /tmp/ff/failure_report.json
```

## Recipe Format

```toml
[recipes.MyFeeds]
urls = [
    "https://example.com/feed.xml",
    "https://example.com/rss",
]
# filters = [{ title = "pattern to exclude", invert = true }]
# fulfill = true  # fetch full article content
```

Also supports OPML files — just drop `.opml` files into the `recipes/` directory.

