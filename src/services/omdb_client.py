import logging
import httpx
from typing import Optional, Any
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings

logger = logging.getLogger("services.omdb_client")


@dataclass
class OMDBMetadata:
    """Structured OMDB query results for series or movies."""
    omdb_id: str  # e.g., 'tt1234567'
    title: str
    year: str
    poster_url: Optional[str]
    overview: str
    vote_average: float


class OMDBClient:
    """Async OMDB API client for metadata enrichment (`httpx` + `tenacity`)."""

    BASE_URL = "http://www.omdbapi.com/"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.OMDB_API_KEY
        if not self.api_key:
            logger.warning("OMDB_API_KEY not set. OMDB enrichment will be skipped.")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def search_media(
        self, query_title: str, year: Optional[int] = None
    ) -> Optional[OMDBMetadata]:
        """Search OMDB for a exact title match, optionally filtering by release year."""
        if not self.api_key or not query_title:
            return None

        params = {
            "apikey": self.api_key,
            "t": query_title,
        }
        if year:
            params["y"] = str(year)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

                if data.get("Response") == "False":
                    logger.info(f"No OMDB matches found for title='{query_title}', year={year}. Reason: {data.get('Error')}")
                    return None

                return self._format_result(data)
                
        except httpx.RequestError as e:
            logger.error(f"OMDB HTTP request failed for query='{query_title}': {e}")
            raise  # Let tenacity retry
        except Exception as e:
            logger.error(f"Unexpected error parsing OMDB response for query='{query_title}': {e}")
            return None

    def _format_result(self, item: dict) -> OMDBMetadata:
        """Convert raw OMDB dict into structured OMDBMetadata dataclass."""
        imdb_id = item.get("imdbID", "")
        title = item.get("Title", "Unknown")
        year = item.get("Year", "")
        
        poster_url = item.get("Poster")
        if poster_url == "N/A":
            poster_url = None

        plot = item.get("Plot", "")
        if plot == "N/A":
            plot = ""
            
        rating_str = item.get("imdbRating", "0")
        try:
            vote_average = float(rating_str)
        except ValueError:
            vote_average = 0.0

        return OMDBMetadata(
            omdb_id=imdb_id,
            title=title,
            year=year,
            poster_url=poster_url,
            overview=plot,
            vote_average=vote_average,
        )
