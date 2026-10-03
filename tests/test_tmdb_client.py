import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from src.services.tmdb_client import TMDBClient, TMDBMetadata


@pytest.mark.asyncio
async def test_tmdb_search_media_success():
    """Verify TMDB multi search successfully finds and ranks media."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "id": 12345,
                "media_type": "tv",
                "name": "A Knight of the Seven Kingdoms",
                "first_air_date": "2026-01-15",
                "poster_path": "/k8yARbD9iYn2nRX2HvsopfKDN2r.jpg",
                "backdrop_path": "/backdrop.jpg",
                "overview": "A century before the events of Game of Thrones...",
                "vote_average": 8.5,
                "popularity": 85.0,
            }
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = TMDBClient(api_key="mock_tmdb_key")
        result = await client.search_media("A Knight of the Seven Kingdoms", year=2026)

        assert result is not None
        assert result.tmdb_id == 12345
        assert result.title == "A Knight of the Seven Kingdoms"
        assert result.media_type == "tv"
        assert result.poster_url == "https://image.tmdb.org/t/p/w500/k8yARbD9iYn2nRX2HvsopfKDN2r.jpg"
        assert result.vote_average == 8.5


@pytest.mark.asyncio
async def test_tmdb_download_poster(tmp_path):
    """Verify TMDBClient downloads poster bytes to the target destination."""
    dest = tmp_path / "poster.jpg"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake_jpeg_content_exceeding_1000_bytes_" * 50

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = TMDBClient(api_key="mock_tmdb_key")
        success = await client.download_poster("https://image.tmdb.org/t/p/w500/sample.jpg", dest)

        assert success is True
        assert dest.exists()
        assert len(dest.read_bytes()) > 1000
