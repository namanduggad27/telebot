import pytest
from src.services.omdb_client import OMDBClient


def test_format_result():
    """Verify clean dataclass construction from raw OMDB response dictionary."""
    client = OMDBClient(api_key="mock_key")
    raw_item = {
        "Title": "Game of Thrones",
        "Year": "2011–2019",
        "imdbID": "tt0944947",
        "Poster": "https://m.media-amazon.com/images/M/dummy.jpg",
        "Plot": "Nine noble families fight for control over the lands of Westeros.",
        "imdbRating": "9.2",
        "Response": "True",
    }

    metadata = client._format_result(raw_item)
    assert metadata.omdb_id == "tt0944947"
    assert metadata.title == "Game of Thrones"
    assert metadata.year == "2011–2019"
    assert metadata.vote_average == 9.2
    assert metadata.poster_url == "https://m.media-amazon.com/images/M/dummy.jpg"
    assert "Westeros" in metadata.overview


def test_format_result_na_values():
    """Verify handling of 'N/A' poster and plot from OMDB."""
    client = OMDBClient(api_key="mock_key")
    raw_item = {
        "Title": "Unknown Show",
        "Year": "2020",
        "imdbID": "tt0000000",
        "Poster": "N/A",
        "Plot": "N/A",
        "imdbRating": "N/A",
        "Response": "True",
    }

    metadata = client._format_result(raw_item)
    assert metadata.poster_url is None
    assert metadata.overview == ""
    assert metadata.vote_average == 0.0
