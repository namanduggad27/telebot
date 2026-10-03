import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from pathlib import Path
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings

logger = logging.getLogger("services.tmdb_client")


@dataclass
class TMDBMetadata:
    """Structured TMDB query results for series or movies."""
    tmdb_id: int
    title: str
    media_type: str         # "tv" or "movie"
    overview: str
    poster_url: Optional[str]
    backdrop_url: Optional[str]
    release_date: Optional[str]
    vote_average: float


class TMDBClient:
    """Async TMDB API client for metadata enrichment and official poster retrieval."""

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
    IMAGE_ORIGINAL_URL = "https://image.tmdb.org/t/p/original"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or settings.TMDB_API_KEY
        if not self.api_key:
            logger.warning("TMDB_API_KEY not configured. TMDB enrichment will be skipped.")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=False,
    )
    async def search_media(
        self, query_title: str, year: Optional[int] = None
    ) -> Optional[TMDBMetadata]:
        """Search TMDB for matching media title, scoring by title similarity and release year."""
        if not self.api_key or not query_title:
            return None

        params: Dict[str, Any] = {
            "api_key": self.api_key,
            "query": query_title,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(f"{self.BASE_URL}/search/multi", params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"TMDB search request failed for query='{query_title}': {e}")
                return None

            results = data.get("results", [])
            if not results:
                results = await self._fallback_search(client, query_title, year)

            if not results:
                logger.info(f"No TMDB matches found for title='{query_title}', year={year}")
                return None

            best_match = self._pick_best_match(results, query_title, year)
            if not best_match:
                return None

            return self._format_result(best_match)

    async def _fallback_search(
        self, client: httpx.AsyncClient, query_title: str, year: Optional[int]
    ) -> List[dict]:
        """Fallback to /search/tv and /search/movie if /search/multi yielded empty results."""
        params: Dict[str, Any] = {"api_key": self.api_key, "query": query_title, "language": "en-US"}
        if year:
            params["first_air_date_year"] = str(year)

        try:
            tv_resp = await client.get(f"{self.BASE_URL}/search/tv", params=params)
            tv_data = tv_resp.json()
            tv_results = tv_data.get("results", [])
            for r in tv_results:
                r["media_type"] = "tv"
            if tv_results:
                return tv_results
        except Exception as e:
            logger.debug(f"Fallback TV search failed: {e}")

        if year:
            params.pop("first_air_date_year", None)
            params["year"] = str(year)
        try:
            movie_resp = await client.get(f"{self.BASE_URL}/search/movie", params=params)
            movie_data = movie_resp.json()
            movie_results = movie_data.get("results", [])
            for r in movie_results:
                r["media_type"] = "movie"
            return movie_results
        except Exception as e:
            logger.debug(f"Fallback Movie search failed: {e}")
            return []

    def _pick_best_match(self, results: list, query_title: str, year: Optional[int]) -> Optional[dict]:
        """Rank results by title similarity, type, and year proximity."""
        query_lower = query_title.lower().strip()
        best_item = None
        best_score = -1.0

        for item in results:
            media_type = item.get("media_type")
            if media_type not in ("tv", "movie"):
                continue

            title = item.get("name") if media_type == "tv" else item.get("title")
            if not title:
                continue

            score = 0.0
            title_lower = title.lower().strip()
            if title_lower == query_lower:
                score += 50.0
            elif query_lower in title_lower or title_lower in query_lower:
                score += 25.0

            date_str = item.get("first_air_date") if media_type == "tv" else item.get("release_date")
            if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                item_year = int(date_str[:4])
                if year and item_year == year:
                    score += 30.0
                elif year and abs(item_year - year) <= 1:
                    score += 15.0

            # Favor items with posters
            if item.get("poster_path"):
                score += 10.0

            popularity = item.get("popularity", 0.0)
            score += min(float(popularity) / 10.0, 15.0)

            if score > best_score:
                best_score = score
                best_item = item

        return best_item

    def _format_result(self, item: dict) -> TMDBMetadata:
        """Convert raw TMDB dict into structured TMDBMetadata."""
        media_type = item.get("media_type", "tv")
        title = item.get("name") if media_type == "tv" else item.get("title", "Unknown")
        date_str = item.get("first_air_date") if media_type == "tv" else item.get("release_date")
        poster_path = item.get("poster_path")
        backdrop_path = item.get("backdrop_path")

        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None
        backdrop_url = f"{self.IMAGE_ORIGINAL_URL}{backdrop_path}" if backdrop_path else None

        return TMDBMetadata(
            tmdb_id=item.get("id", 0),
            title=title,
            media_type=media_type,
            overview=item.get("overview", "")[:800],
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            release_date=date_str,
            vote_average=float(item.get("vote_average", 0.0)),
        )

    async def download_poster(self, poster_url: str, dest_path: Path) -> bool:
        """Download TMDB poster to a local file."""
        if not poster_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(poster_url)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(resp.content)
                    return True
        except Exception as e:
            logger.warning(f"Failed to download TMDB poster from {poster_url}: {e}")
        return False
