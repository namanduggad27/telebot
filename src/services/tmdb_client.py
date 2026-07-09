import logging
from dataclasses import dataclass
from typing import Optional, Tuple
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

logger = logging.getLogger("services.tmdb_client")


@dataclass
class TMDBMetadata:
    """Structured TMDB query results for series or movies."""
    tmdb_id: int
    title: str
    original_title: str
    media_type: str         # "tv" or "movie"
    overview: str
    poster_url: Optional[str]
    backdrop_url: Optional[str]
    release_date: Optional[str]
    vote_average: float


class TMDBClient:
    """Async TMDB API client for metadata enrichment (`httpx` + `tenacity`)."""

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p/original"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or settings.TMDB_API_KEY

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def search_media(
        self, query_title: str, year: Optional[int] = None
    ) -> Optional[TMDBMetadata]:
        """Search TMDB multi-index for a clean title string, optionally filtering/scoring by release year."""
        if not self.api_key:
            logger.warning("TMDB_API_KEY not provided in settings. Skipping metadata enrichment.")
            return None

        params = {
            "api_key": self.api_key,
            "query": query_title,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{self.BASE_URL}/search/multi", params=params)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"TMDB HTTP request failed for query='{query_title}': {e}")
                raise

            results = data.get("results", [])
            if not results:
                # Fallback: if multi search returned nothing, try specific TV or Movie search
                results = await self._fallback_search(client, query_title, year)

            if not results:
                logger.info(f"No TMDB matches found for title='{query_title}', year={year}")
                return None

            # Score and pick best match (prioritize exact title match and year match)
            best_match = self._pick_best_match(results, query_title, year)
            if not best_match:
                return None

            return self._format_result(best_match)

    async def _fallback_search(
        self, client: httpx.AsyncClient, query_title: str, year: Optional[int]
    ) -> list:
        """Fallback queries to /search/tv and /search/movie if /search/multi yielded empty results."""
        params = {"api_key": self.api_key, "query": query_title, "language": "en-US"}
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

        # Try movie search if TV returned empty
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
        """Rank results by title similarity and year proximity."""
        query_lower = query_title.lower().strip()
        best_item = None
        best_score = -1

        for item in results:
            media_type = item.get("media_type")
            if media_type not in ("tv", "movie"):
                continue

            title = item.get("name") if media_type == "tv" else item.get("title")
            if not title:
                continue

            score = 0
            # Exact title check
            if title.lower().strip() == query_lower:
                score += 50
            elif query_lower in title.lower() or title.lower() in query_lower:
                score += 20

            # Year check
            date_str = item.get("first_air_date") if media_type == "tv" else item.get("release_date")
            item_year = None
            if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                item_year = int(date_str[:4])
                if year and item_year == year:
                    score += 30
                elif year and abs(item_year - year) <= 1:
                    score += 15

            # Popularity boost
            popularity = item.get("popularity", 0)
            score += min(popularity / 10.0, 15)

            if score > best_score:
                best_score = score
                best_item = item

        return best_item

    def _format_result(self, item: dict) -> TMDBMetadata:
        """Convert raw TMDB dict into structured TMDBMetadata dataclass."""
        media_type = item.get("media_type", "tv")
        title = item.get("name") if media_type == "tv" else item.get("title", "Unknown")
        original_title = item.get("original_name") if media_type == "tv" else item.get("original_title", title)
        date_str = item.get("first_air_date") if media_type == "tv" else item.get("release_date")
        
        poster_path = item.get("poster_path")
        backdrop_path = item.get("backdrop_path")

        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None
        backdrop_url = f"{self.IMAGE_BASE_URL}{backdrop_path}" if backdrop_path else None

        return TMDBMetadata(
            tmdb_id=item.get("id", 0),
            title=title,
            original_title=original_title,
            media_type=media_type,
            overview=item.get("overview", "")[:800],  # Truncate if overly long
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            release_date=date_str,
            vote_average=float(item.get("vote_average", 0.0)),
        )
