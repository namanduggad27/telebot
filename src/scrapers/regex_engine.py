import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ParsedMedia:
    """Structured result from regex title parsing."""
    raw_title: str
    clean_title: str
    year: Optional[int]
    season_num: Optional[int]
    episode_num: Optional[int]
    quality: Optional[str]      # e.g. "1080p WEB-DL"
    codec: Optional[str]        # e.g. "x265", "x264", "HEVC"
    audio: Optional[str]        # e.g. "DDP5.1", "AAC"
    is_season_pack: bool
    clean_file_name: str        # Standardized initial suggestion, e.g. "Show.Name.2023.S01E02.1080p.mkv"


class RegexEngine:
    """Robust regex parsing engine for extracting series/movie metadata from scene release filenames."""

    # Patterns for Season and Episode numbering
    SE_PATTERNS = [
        # Standard S01E01, S01E01-E02, s1e1
        re.compile(r"(?i)[ ._-]*[Ss](?P<season>\d{1,2})[ ._-]*[Ee](?P<episode>\d{1,4})(?:-[Ee]?\d{1,4})?[ ._-]*"),
        # Season X Episode Y, 1x01, 12x14
        re.compile(r"(?i)[ ._-]+(?P<season>\d{1,2})[xX](?P<episode>\d{1,4})[ ._-]+"),
        # Episode only Ep01, E01, Episode 1
        re.compile(r"(?i)[ ._-]+(?:[Ee][Pp]?[ ._-]*|Episode[ ._-]+)(?P<episode>\d{1,4})[ ._-]+"),
        # Season pack S01, Season 1
        re.compile(r"(?i)[ ._-]*[Ss](?P<season>\d{1,2})(?![0-9xXeE])[ ._-]*"),
        re.compile(r"(?i)[ ._-]+Season[ ._-]+(?P<season>\d{1,2})[ ._-]+"),
    ]

    # Year pattern (19xx - 20xx)
    YEAR_PATTERN = re.compile(r"(?i)[ ._-]+(?P<year>(?:19|20)\d{2})(?![0-9pP])")

    # Quality and resolution tags
    RESOLUTION_PATTERN = re.compile(r"(?i)\b(?P<res>2160p|4k|1080p|1080i|720p|480p|360p)\b")
    SOURCE_PATTERN = re.compile(r"(?i)\b(?P<source>WEB[ -]?DL|WEBRip|BluRay|BDRip|HDRip|HDTV|DVD|DVDRip|AMZN|NF|DSNP|HMAX)\b")

    # Video Codec
    CODEC_PATTERN = re.compile(r"(?i)\b(?P<codec>x265|x264|h[ .]?265|h[ .]?264|HEVC|AVC|AV1|VP9)\b")

    # Audio Format
    AUDIO_PATTERN = re.compile(r"(?i)\b(?P<audio>DDP5\.1|DD5\.1|AC3|AAC|MP3|FLAC|TrueHD|Atmos|DTS-HD|DTS)\b")

    # File extension
    EXT_PATTERN = re.compile(r"\.(?P<ext>mkv|mp4|avi|mov|m4v)$", re.IGNORECASE)

    @classmethod
    def parse(cls, filename: str) -> ParsedMedia:
        """Parse a media release filename or title string into structured metadata."""
        clean_name = filename.strip()
        
        # 1. Extract file extension
        ext_match = cls.EXT_PATTERN.search(clean_name)
        ext = ext_match.group("ext").lower() if ext_match else "mkv"
        name_no_ext = cls.EXT_PATTERN.sub("", clean_name)

        # Normalize underscores, hyphens, and dots to spaces for clean word boundary matching (\b)
        normalized_name = re.sub(r"[._-]+", " ", name_no_ext)

        # 2. Extract resolution and source quality
        res_matches = cls.RESOLUTION_PATTERN.findall(normalized_name)
        source_matches = cls.SOURCE_PATTERN.findall(normalized_name)
        res_str = res_matches[0].upper() if res_matches else ""
        source_str = " ".join([s.upper().replace("WEB DL", "WEB-DL") for s in source_matches]) if source_matches else ""
        quality = f"{res_str} {source_str}".strip() or None

        # 3. Extract codec and audio
        codec_match = cls.CODEC_PATTERN.search(normalized_name)
        codec = codec_match.group("codec").upper().replace("H 264", "H.264").replace("H 265", "H.265").replace("X264", "x264").replace("X265", "x265") if codec_match else None

        audio_match = cls.AUDIO_PATTERN.search(normalized_name)
        audio = audio_match.group("audio") if audio_match else None

        # 4. Extract Season & Episode
        season_num: Optional[int] = None
        episode_num: Optional[int] = None
        is_season_pack = False
        match_idx = len(name_no_ext)

        for i, pattern in enumerate(cls.SE_PATTERNS):
            se_match = pattern.search(name_no_ext)
            if se_match:
                match_idx = se_match.start()
                if "season" in se_match.groupdict() and se_match.group("season"):
                    season_num = int(se_match.group("season"))
                if "episode" in se_match.groupdict() and se_match.group("episode"):
                    episode_num = int(se_match.group("episode"))
                
                # If pattern 3 or 4 matched without episode, it's a season pack
                if i in (3, 4) and episode_num is None:
                    is_season_pack = True
                break

        # 5. Extract Year and find where title ends
        year: Optional[int] = None
        year_match = cls.YEAR_PATTERN.search(name_no_ext[:match_idx])
        if year_match:
            year = int(year_match.group("year"))
            match_idx = min(match_idx, year_match.start())

        # If no SE match found, check if quality/res tag marks the end of title
        if match_idx == len(name_no_ext) and res_matches:
            res_pos = cls.RESOLUTION_PATTERN.search(name_no_ext)
            if res_pos:
                match_idx = min(match_idx, res_pos.start())

        # 6. Extract clean title
        raw_title_slice = name_no_ext[:match_idx]
        clean_title = re.sub(r"[._-]+", " ", raw_title_slice).strip()
        # Clean up any leftover brackets or leading/trailing garbage
        clean_title = re.sub(r"^[\[\(]+|[\]\)]+$", "", clean_title).strip()
        if not clean_title:
            clean_title = name_no_ext.split(".")[0].replace("_", " ")

        # 7. Construct suggested clean filename
        parts = [clean_title]
        if year:
            parts.append(f"({year})")
        if season_num is not None and episode_num is not None:
            parts.append(f"S{season_num:02d}E{episode_num:02d}")
        elif season_num is not None:
            parts.append(f"S{season_num:02d}")
        elif episode_num is not None:
            parts.append(f"E{episode_num:02d}")
        
        if quality:
            parts.append(f"[{quality}]")
        elif res_str:
            parts.append(f"[{res_str}]")

        clean_file_name = " - ".join(parts) + f".{ext}"

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
