from models import FeedConfig, FeedFilter, RecipeCollection

# Define recipes using Pydantic models
_recipe_data = RecipeCollection(
    recipes={
        "GithuberFeeds": FeedConfig(
            urls=[
                "https://github.com/norsez.atom",
                "https://github.com/nst.atom",
                "https://github.com/omz.atom",
                "https://github.com/peng-zhihui.atom",
                "https://github.com/someone-not-found-blabla.atom",
            ],
            filters=[
                FeedFilter(
                    title="commented on|closed an issue|opened an issue|merged a pull request|pushed to|deleted branch|created a tag|created a branch",
                    invert=True,
                ),
            ],
        ),
        "Rust News": FeedConfig(
            urls=[
                "https://hnrss.org/frontpage.atom?q=rust",
                "https://www.reddit.com/r/rust.rss",
            ]
        ),
    }
)


def get_recipes() -> dict[str, FeedConfig]:
    """Return all feed recipes as dictionary mapping feed names to their configurations"""
    return _recipe_data.recipes


if __name__ == "__main__":
    import json

    print(json.dumps({k: v.model_dump() for k, v in get_recipes().items()}, indent=2))
