from pathlib import Path

import pytest

from feedforger.recipes import load_recipes


def write_recipe(path: Path, filters: str) -> None:
    path.write_text(
        f"""
[recipes.News]
urls = ["https://example.com/feed.xml"]
filters = [{filters}]
""".strip()
    )


def test_load_recipes_accepts_title_filters(tmp_path: Path) -> None:
    recipe_path = tmp_path / "recipes.toml"
    write_recipe(recipe_path, '{ title = "sponsored", invert = true }')

    recipes = load_recipes(recipe_path)

    assert recipes["News"].model_dump() == {
        "urls": ["https://example.com/feed.xml"],
        "filters": [{"title": "sponsored", "invert": True}],
        "fulfill": False,
    }


def test_load_recipes_rejects_filter_without_title(tmp_path: Path) -> None:
    recipe_path = tmp_path / "recipes.toml"
    write_recipe(recipe_path, '{ author = "Ada" }')

    with pytest.raises(ValueError, match=r"recipes\.News\.filters\.0\.title"):
        load_recipes(recipe_path)
