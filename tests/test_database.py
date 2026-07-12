import asyncio
from datetime import UTC, datetime
from pathlib import Path

from feedforger.db import Database, FailureReport


def test_failure_report_returns_finished_payload(tmp_path: Path) -> None:
    async def get_report() -> FailureReport:
        async with Database(db_path=tmp_path / "feeds.sqlite") as db:
            await db.set_content(
                url="https://ok.example/feed",
                content="feed content",
            )
            await db.set_content(
                url="https://bad.example/feed",
                content=None,
                success=False,
                error_reason="timeout",
            )
            return await db.get_failure_report()

    report = asyncio.run(get_report())

    assert set(report) == {
        "generated_at",
        "generated_at_iso",
        "total",
        "failing",
        "entries",
    }
    assert (
        report["generated_at_iso"]
        == datetime.fromtimestamp(report["generated_at"], UTC).isoformat()
    )
    assert report["total"] == 2
    assert report["failing"] == 1
    assert [entry["url"] for entry in report["entries"]] == [
        "https://bad.example/feed",
        "https://ok.example/feed",
    ]
    assert report["entries"][0]["continue_fail_count"] == 1
    assert report["entries"][0]["error_reason"] == "timeout"
