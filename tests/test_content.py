from datetime import UTC, datetime
from typing import Any

import pytest

import feedforger.content as content_module
from feedforger.content import build_item_content, needs_fulfillment
from feedforger.models import FeedItem

PUBLISHED = datetime(2026, 7, 10, 12, tzinfo=UTC)
FEED_META = {
    "title": "Fixture Source",
    "link": "https://source.example/",
}


def build(
    entry: dict[str, Any],
    *,
    page_html: str | None = None,
) -> FeedItem | None:
    return build_item_content(
        entry=entry,
        feed_meta=FEED_META,
        published=PUBLISHED,
        feed_language="en",
        source_url="https://source.example/feed.xml",
        page_html=page_html,
    )


def test_build_item_content_extracts_embedded_content() -> None:
    item = build(
        {
            "id": "urn:item:1",
            "link": "https://source.example/items/1",
            "title": "Embedded item",
            "content": [
                {
                    "type": "text/html",
                    "value": (
                        "<article><p>Hello <strong>world</strong>.</p>"
                        "<!-- remove --><script>bad()</script></article>"
                    ),
                }
            ],
            "summary": "<p>Ignored feed summary.</p>",
            "author": {
                "name": "Ada",
                "uri": "https://source.example/authors/ada",
            },
            "tags": [{"term": "Python"}, {"term": ""}],
            "media_thumbnail": [{"url": "https://source.example/thumbnail.jpg"}],
            "source": {"href": "https://links.example/original"},
        }
    )

    assert item is not None
    assert item.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "id": "urn:item:1",
        "url": "https://source.example/items/1",
        "title": "Embedded item",
        "content_html": ("<article><p>Hello <strong>world</strong>.</p></article>"),
        "summary": "Hello world .",
        "date_published": "2026-07-10T12:00:00Z",
        "author": {
            "name": "Ada",
            "url": "https://source.example/authors/ada",
        },
        "tags": ["Python"],
        "language": "en",
        "image": "https://source.example/thumbnail.jpg",
        "external_url": "https://links.example/original",
        "_source": {
            "title": "Fixture Source",
            "url": "https://source.example/feed.xml",
            "home_page_url": "https://source.example/",
        },
    }


def test_fetched_page_overrides_content_and_upgrades_short_title() -> None:
    item = build(
        {
            "link": "https://source.example/items/2",
            "title": "Short",
            "content": [{"type": "text/html", "value": "<p>Brief.</p>"}],
        },
        page_html="""
        <html>
          <head><title>A much longer page title</title></head>
          <body>
            <nav>Noise</nav>
            <article>
              <img src="https://source.example/page.jpg">
              <p>Full <strong>article</strong>.</p><script>bad()</script>
            </article>
          </body>
        </html>
        """,
    )

    assert item is not None
    assert item.title == "A much longer page title"
    assert item.content_html == (
        '<article>\n<img src="https://source.example/page.jpg"/>\n'
        "<p>Full <strong>article</strong>.</p>\n</article>"
    )
    assert item.content_text == "Full article ."
    assert item.summary == "Brief."
    assert str(item.image) == "https://source.example/page.jpg"


def test_page_without_extractable_content_keeps_embedded_content() -> None:
    item = build(
        {
            "link": "https://source.example/items/3",
            "title": "Embedded title",
            "content": [{"type": "text/html", "value": "<p>Embedded.</p>"}],
        },
        page_html="<html><head><title>Unused title</title></head></html>",
    )

    assert item is not None
    assert item.title == "Embedded title"
    assert item.content_html == "<p>Embedded.</p>"
    assert item.content_text is None


def test_page_sanitization_without_content_keeps_embedded_content() -> None:
    item = build(
        {
            "link": "https://source.example/items/4",
            "title": "Embedded title",
            "content": [{"type": "text/html", "value": "<p>Embedded.</p>"}],
        },
        page_html=(
            "<html><head><title>Unused title</title></head>"
            "<body><article><script>bad()</script></article></body></html>"
        ),
    )

    assert item is not None
    assert item.title == "Embedded title"
    assert item.content_html == "<p>Embedded.</p>"
    assert item.content_text is None


def test_sanitizer_failure_preserves_embedded_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_parser(*args: object, **kwargs: object) -> None:
        raise ValueError("parser unavailable")

    monkeypatch.setattr(content_module, "BeautifulSoup", fail_parser)
    raw_html = "<p>Keep this HTML when sanitization fails.</p>"

    item = build(
        {
            "link": "https://source.example/items/sanitizer-failure",
            "title": "Sanitizer failure",
            "content": [{"type": "text/html", "value": raw_html}],
            "summary": "Fallback summary.",
            "media_thumbnail": [{"url": "https://source.example/image.jpg"}],
        }
    )

    assert item is not None
    assert item.content_html == raw_html
    assert item.summary == "Fallback summary."


def test_page_parser_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_parser(*args: object, **kwargs: object) -> None:
        raise ValueError("parser unavailable")

    monkeypatch.setattr(content_module, "BeautifulSoup", fail_parser)

    with pytest.raises(
        RuntimeError,
        match="Failed to extract Content from https://source.example/items/parser-failure",
    ):
        build(
            {
                "link": "https://source.example/items/parser-failure",
                "title": "Parser failure",
                "summary": "Embedded summary.",
            },
            page_html="<article>Fetched content.</article>",
        )


@pytest.mark.parametrize(
    ("entry_fields", "expected"),
    [
        pytest.param(
            {
                "media_content": [
                    {
                        "medium": "image",
                        "url": "https://source.example/media.jpg",
                    }
                ],
                "media_thumbnail": [{"url": "https://source.example/thumbnail.jpg"}],
            },
            "https://source.example/media.jpg",
            id="media-content",
        ),
        pytest.param(
            {
                "media_thumbnail": [{"url": "https://source.example/thumbnail.jpg"}],
                "image": {"href": "https://source.example/atom.jpg"},
            },
            "https://source.example/thumbnail.jpg",
            id="media-thumbnail",
        ),
        pytest.param(
            {
                "image": {"href": "https://source.example/atom.jpg"},
                "enclosures": [
                    {
                        "type": "image/png",
                        "href": "https://source.example/enclosure.png",
                    }
                ],
            },
            "https://source.example/atom.jpg",
            id="atom-image",
        ),
        pytest.param(
            {
                "enclosures": [
                    {
                        "type": "image/png",
                        "href": "https://source.example/enclosure.png",
                    }
                ]
            },
            "https://source.example/enclosure.png",
            id="enclosure",
        ),
        pytest.param(
            {},
            "https://source.example/inline.jpg",
            id="first-content-image",
        ),
    ],
)
def test_image_fallback_chain(entry_fields: dict[str, Any], expected: str) -> None:
    item = build(
        {
            "link": "https://source.example/items/image",
            "title": "Image item",
            "content": [
                {
                    "type": "text/html",
                    "value": (
                        '<p><img src="https://source.example/inline.jpg">Body.</p>'
                    ),
                }
            ],
            **entry_fields,
        }
    )

    assert item is not None
    assert str(item.image) == expected


def test_summary_truncation_uses_documented_limits() -> None:
    plain_item = build(
        {
            "link": "https://source.example/items/plain",
            "title": "Plain item",
            "summary": "x" * 400,
        }
    )
    html_item = build(
        {
            "link": "https://source.example/items/html",
            "title": "HTML item",
            "content": [{"type": "text/html", "value": f"<p>{'y' * 400}</p>"}],
        }
    )

    assert plain_item is not None
    assert plain_item.summary == "x" * 319 + "…"
    assert html_item is not None
    assert html_item.summary == "y" * 279 + "…"


def test_fulfillment_judgment_uses_content_size() -> None:
    item = FeedItem.model_validate(
        {
            "id": "item",
            "url": "https://source.example/items/size",
            "title": "Size item",
            "content_html": "x" * 700,
            "date_published": PUBLISHED,
        }
    )
    assert needs_fulfillment(item)

    item.content_html = "x" * 701
    assert not needs_fulfillment(item)

    item.content_html = None
    item.content_text = "x" * 400
    assert needs_fulfillment(item)

    item.content_text = "x" * 401
    assert not needs_fulfillment(item)


def test_build_item_content_skips_entries_without_title_or_link() -> None:
    assert build({"title": "Missing link"}) is None
    assert build({"link": "https://source.example/items/missing-title"}) is None
