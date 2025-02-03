_recipes = {
    "GithuberFeeds": {
        "urls": [
            "https://github.com/norsez.atom",
            "https://github.com/nst.atom",
            "https://github.com/omz.atom",
            "https://github.com/peng-zhihui.atom",
            "https://github.com/qhduan.atom",
            "https://github.com/rentzsch.atom",
            "https://github.com/rnapier.atom",
            "https://github.com/robb.atom",
            "https://github.com/samvermette.atom",
            "https://github.com/stefanoa.atom",
            "https://github.com/theowenyoung.atom",
            "https://github.com/ishan0102.atom",
        ],
        "filters": [
            {
                "title": "commented on|closed an issue|opened an issue|merged a pull request|pushed to|deleted branch|created a tag|created a branch",
                "invert": True,
            },
        ],
    },
    "Rust News": {
        "urls": [
            "https://hnrss.org/frontpage.atom?q=rust",
            "https://www.reddit.com/r/rust.rss",
        ]
    },
}


def get_recipes() -> dict[str, dict]:
    return _recipes


if __name__ == "__main__":
    import json

    print(json.dumps(get_recipes()))
