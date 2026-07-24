import re
from dataclasses import dataclass
from typing import Optional
from guessit import guessit
from config.settings import settings


@dataclass
class ParsedMedia:
    """Structured result from filename parsing."""
    raw_title: str
    clean_title: str
    year: Optional[int]
    season_num: Optional[int]
    episode_num: Optional[int]
    quality: Optional[str]      # e.g. "1080p WEB-DL"
    codec: Optional[str]        # e.g. "x265", "x264", "HEVC"
    audio: Optional[str]        # e.g. "DDP5.1", "AAC"
    is_season_pack: bool
    clean_file_name: str        # Standardized initial suggestion


class RegexEngine:
    """Robust parsing engine for extracting series/movie metadata from scene release filenames using GuessIt."""

    # File extension
    EXT_PATTERN = re.compile(r"\.(?P<ext>mkv|mp4|avi|mov|m4v)$", re.IGNORECASE)

    @classmethod
    def parse(cls, filename: str) -> ParsedMedia:
        """Parse a media release filename or title string into structured metadata using GuessIt."""
        clean_name = filename.strip()
        
        # 1. Extract file extension
        ext_match = cls.EXT_PATTERN.search(clean_name)
        ext = ext_match.group("ext").lower() if ext_match else "mkv"
        name_no_ext = cls.EXT_PATTERN.sub("", clean_name)

        # 2. Run guessit
        guess = guessit(name_no_ext)
        
        # 3. Extract core properties
        clean_title = guess.get("title", name_no_ext.replace(".", " "))
        year = guess.get("year")
        
        # Guessit returns lists if multiple seasons/episodes
        seasons = guess.get("season")
        if isinstance(seasons, list):
            season_num = seasons[0]
        else:
            season_num = seasons
            
        episodes = guess.get("episode")
        if isinstance(episodes, list):
            episode_num = episodes[0]
        else:
            episode_num = episodes
            
        is_season_pack = (season_num is not None and episode_num is None)
        
        # 4. Extract quality tags
        resolution = guess.get("screen_size", "")
        source = guess.get("source", "")
        quality_parts = []
        if resolution:
            quality_parts.append(str(resolution))
        if source:
            quality_parts.append(str(source).upper())
        quality = " ".join(quality_parts) or None
        
        codec = guess.get("video_codec")
        if isinstance(codec, list):
            codec = codec[0]
            
        audio = guess.get("audio_codec")
        if isinstance(audio, list):
            audio = audio[0]

        # 5. Construct suggested clean filename using the RENAME_FORMAT setting
        format_kwargs = {
            "title": clean_title,
            "season": season_num if season_num is not None else 0,
            "episode": episode_num if episode_num is not None else 0,
            "quality": quality or "Unknown",
            "ext": ext
        }
        
        try:
            clean_file_name = settings.RENAME_FORMAT.format(**format_kwargs)
        except KeyError:
            # Fallback if format string is malformed
            clean_file_name = f"{clean_title} - S{season_num or 0:02d}E{episode_num or 0:02d} - [{quality or 'Unknown'}].{ext}"

        return ParsedMedia(
            raw_title=filename,
            clean_title=clean_title,
            year=year,
            season_num=season_num,
            episode_num=episode_num,
            quality=quality,
            codec=codec,
            audio=audio,
            is_season_pack=is_season_pack,
            clean_file_name=clean_file_name,
        )

    @classmethod
    def format_custom_name(cls, item, fmt_string: str) -> str:
        """Format a media item into a clean string using the provided format string."""
        ext_match = cls.EXT_PATTERN.search(item.clean_file_name or "")
        ext = ext_match.group("ext").lower() if ext_match else "mkv"
        
        format_kwargs = {
            "title": item.parsed_title or "Unknown",
            "season": item.season_num if item.season_num is not None else 0,
            "episode": item.episode_num if item.episode_num is not None else 0,
            "quality": item.quality_tag or "Unknown",
            "ext": ext
        }
        
        try:
            return fmt_string.format(**format_kwargs)
        except KeyError:
            # Fallback if format string is malformed
            return f"{format_kwargs['title']} - S{format_kwargs['season']:02d}E{format_kwargs['episode']:02d} - [{format_kwargs['quality']}].{ext}"
