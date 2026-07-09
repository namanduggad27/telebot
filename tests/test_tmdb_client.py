import pytest
from src.services.tmdb_client import TMDBClient


def test_pick_best_match_exact_title():
    """Verify that _pick_best_match correctly identifies and ranks exact title matches with year proximity."""
    client = TMDBClient(api_key="mock_key")
    mock_results = [
        {
            "id": 101,
            "media_type": "tv",
            "name": "Breaking Bad Unofficial",
            "first_air_date": "2015-01-01",
            "popularity": 10.0,
        },
        {
            "id": 102,
            "media_type": "tv",
            "name": "Breaking Bad",
            "first_air_date": "2008-01-20",
            "popularity": 95.0,
        },
    ]

    best = client._pick_best_match(mock_results, query_title="Breaking Bad", year=2008)
    assert best is not None
    assert best["id"] == 102
    assert best["name"] == "Breaking Bad"


def test_format_result():
    """Verify clean dataclass construction from raw TMDB response dictionary."""
    client = TMDBClient(api_key="mock_key")
    raw_item = {
        "id": 1399,
        "media_type": "tv",
        "name": "Game of Thrones",
        "original_name": "Game of Thrones",
        "overview": "Seven noble families fight for control of the mythical land of Westeros.",
        "poster_path": "/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg",
        "backdrop_path": "/6Lw54zxm6BAEKJeGlabyzzR5Juu.jpg",
        "first_air_date": "2011-04-17",
        "vote_average": 8.44,
    }

    metadata = client._format_result(raw_item)
    assert metadata.tmdb_id == 1399
    assert metadata.title == "Game of Thrones"
    assert metadata.media_type == "tv"
    assert metadata.vote_average == 8.44
    assert metadata.poster_url == "https://image.tmdb.org/t/p/original/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg"
