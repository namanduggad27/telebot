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
        # Audio tag and language detection
        audio_tag = "Eng"
        audio_full = "English"
        name_lower = filename.lower()
        if "hindi" in name_lower and "eng" in name_lower:
            audio_tag = "Dual"
            audio_full = "Hindi - English"
        elif "hindi" in name_lower:
            audio_tag = "Hin"
            audio_full = "Hindi"
        elif "tam" in name_lower:
            audio_tag = "Tam"
            audio_full = "Tamil"
        elif "tel" in name_lower:
            audio_tag = "Tel"
            audio_full = "Telugu"

        clean_title_compact = "".join(
            word.capitalize() for word in re.sub(r"[^\w\s]", "", clean_title).split()
        ) or "Unknown"

        raw_quality = quality or "1080p"
        quality_clean = raw_quality.split()[0] if raw_quality else "1080p"

        raw_codec = codec or "x265"
        codec_clean = (
            str(raw_codec).lower().replace("hevc", "265").replace("h.265", "265").replace("h.264", "264").replace("x", "")
        ) or "265"

        format_kwargs = {
            "title": clean_title,
            "raw_title": clean_title,
            "clean_title": clean_title_compact,
            "title_underscore": "_".join(re.sub(r"[^\w\s]", "", clean_title).split()),
            "season": season_num if season_num is not None else 1,
            "episode": episode_num if episode_num is not None else 1,
            "quality": quality_clean,
            "raw_quality": raw_quality,
            "codec": codec_clean,
            "audio": audio_tag,
            "audio_full": audio_full,
            "year": year or 2025,
            "ext": ext,
        }

        try:
            clean_file_name = settings.RENAME_FORMAT.format(**format_kwargs)
        except Exception:
            clean_file_name = f"[TIF]_S{format_kwargs['season']:02d}_E{format_kwargs['episode']:02d}_{clean_title_compact}_{quality_clean}_{audio_tag}.{ext}"

        return ParsedMedia(
            raw_title=filename,
            clean_title=clean_title,
            year=year,
            season_num=season_num,
            episode_num=episode_num,
            quality=quality,
            codec=codec,
            audio=audio_tag,
            is_season_pack=is_season_pack,
            clean_file_name=clean_file_name,
        )

    @classmethod
    def format_custom_name(cls, item, fmt_string: str) -> str:
        """Format a media item into a clean filename using the provided format string."""
        ext_match = cls.EXT_PATTERN.search(getattr(item, "clean_file_name", "") or "")
        ext = ext_match.group("ext").lower() if ext_match else "mkv"

        raw_title = getattr(item, "parsed_title", None) or "Unknown"
        clean_title_compact = "".join(
            word.capitalize() for word in re.sub(r"[^\w\s]", "", raw_title).split()
        ) or "Unknown"
        clean_title_underscores = "_".join(re.sub(r"[^\w\s]", "", raw_title).split())

        raw_quality = getattr(item, "quality_tag", None) or "1080p"
        quality_clean = raw_quality.split()[0] if raw_quality else "1080p"

        raw_codec = getattr(item, "codec_tag", None) or "x265"
        codec_clean = (
            str(raw_codec).lower().replace("hevc", "265").replace("h.265", "265").replace("h.264", "264").replace("x", "")
        ) or "265"

        name_haystack = f"{getattr(item, 'clean_file_name', '')} {raw_title}".lower()
        audio_tag = "Eng"
        audio_full = "English"
        if "hindi" in name_haystack and "eng" in name_haystack:
            audio_tag = "Dual"
            audio_full = "Hindi - English"
        elif "hindi" in name_haystack:
            audio_tag = "Hin"
            audio_full = "Hindi"
        elif "tam" in name_haystack:
            audio_tag = "Tam"
            audio_full = "Tamil"
        elif "tel" in name_haystack:
            audio_tag = "Tel"
            audio_full = "Telugu"

        format_kwargs = {
            "title": clean_title_compact,
            "raw_title": raw_title,
            "clean_title": clean_title_compact,
            "title_underscore": clean_title_underscores,
            "season": item.season_num if item.season_num is not None else 1,
            "episode": item.episode_num if item.episode_num is not None else 1,
            "quality": quality_clean,
            "raw_quality": raw_quality,
            "codec": codec_clean,
            "audio": audio_tag,
            "audio_full": audio_full,
            "year": getattr(item, "year", None) or 2025,
            "ext": ext,
        }

        try:
            return fmt_string.format(**format_kwargs)
        except Exception:
            s = item.season_num if item.season_num is not None else 1
            e = item.episode_num if item.episode_num is not None else 1
            return f"[TIF]_S{s:02d}_E{e:02d}_{clean_title_compact}_{quality_clean}_{audio_tag}.{ext}"

    @classmethod
    def format_caption(cls, item, fmt_string: str, year: Optional[str] = None) -> str:
        """Format a media item's shadow channel video caption according to the provided format string."""
        raw_title = getattr(item, "parsed_title", None) or "Unknown Title"
        clean_title_compact = "".join(
            word.capitalize() for word in re.sub(r"[^\w\s]", "", raw_title).split()
        ) or "Unknown"

        raw_quality = getattr(item, "quality_tag", None) or "1080p"
        quality_clean = raw_quality.split()[0] if raw_quality else "1080p"

        raw_codec = getattr(item, "codec_tag", None) or "x265"
        codec_clean = (
            str(raw_codec).lower().replace("hevc", "265").replace("h.265", "265").replace("h.264", "264").replace("x", "")
        ) or "265"

        name_haystack = f"{getattr(item, 'clean_file_name', '')} {raw_title}".lower()
        audio_tag = "Eng"
        audio_full = "English"
        if "hindi" in name_haystack and "eng" in name_haystack:
            audio_tag = "Dual"
            audio_full = "Hindi - English"
        elif "hindi" in name_haystack:
            audio_tag = "Hin"
            audio_full = "Hindi"
        elif "tam" in name_haystack:
            audio_tag = "Tam"
            audio_full = "Tamil"
        elif "tel" in name_haystack:
            audio_tag = "Tel"
            audio_full = "Telugu"

        se_badge = ""
        se_prefix = ""
        if item.season_num is not None and item.episode_num is not None:
            se_badge = f"S{item.season_num:02d}E{item.episode_num:02d}"
            se_prefix = f"S{item.season_num:02d}E{item.episode_num:02d} "
        elif item.season_num is not None:
            se_badge = f"S{item.season_num:02d}"
            se_prefix = f"S{item.season_num:02d} "

        ext_match = cls.EXT_PATTERN.search(getattr(item, "clean_file_name", "") or "")
        ext = ext_match.group("ext").lower() if ext_match else "mkv"

        format_kwargs = {
            "title": raw_title,
            "raw_title": raw_title,
            "clean_title": clean_title_compact,
            "year": year or getattr(item, "year", None) or "2025",
            "season": item.season_num if item.season_num is not None else 1,
            "episode": item.episode_num if item.episode_num is not None else 1,
            "se_badge": se_badge,
            "se_prefix": se_prefix,
            "quality": quality_clean,
            "raw_quality": raw_quality,
            "codec": codec_clean,
            "audio": audio_full,
            "audio_tag": audio_tag,
            "filename": getattr(item, "clean_file_name", "") or f"{clean_title_compact}.{ext}",
            "ext": ext,
        }

        try:
            return fmt_string.format(**format_kwargs)
        except Exception:
            y = year or getattr(item, "year", None) or "2025"
            return (
                f"🎥 {raw_title} ({y})\n"
                f"**🎬 {se_prefix}{quality_clean}×{codec_clean} Esub 👍**\n"
                f"**📢 Audio: {audio_full}**\n"
                f"༺━━━━━━━━━━━━༻\n"
                f"@TIF_Shoppie🌹@TIF_Network"
            )

    SMALL_CAPS_MAP = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ',
        'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ',
        'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ϙ', 'r': 'ʀ',
        's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ',
    }

    MONOSPACE_DIGITS = {
        '0': '\U0001D7F6', '1': '\U0001D7F7', '2': '\U0001D7F8', '3': '\U0001D7F9', '4': '\U0001D7FA',
        '5': '\U0001D7FB', '6': '\U0001D7FC', '7': '\U0001D7FD', '8': '\U0001D7FE', '9': '\U0001D7FF',
    }

    @classmethod
    def to_small_caps(cls, text: str) -> str:
        """Convert ASCII alphabetic characters to Unicode small capital letters."""
        return "".join(cls.SMALL_CAPS_MAP.get(c.lower(), c) for c in text)

    @classmethod
    def format_quality_stylized(cls, qualities: list[str]) -> str:
        """Format quality tags into stylized small caps and monospace boxes (e.g., ‖ 𝟽𝟸𝟶ᴘ ‖ 𝟷𝟶𝟾𝟶ᴘ ‖)."""
        parts = []
        seen = set()
        for q in qualities:
            q_clean = q.lower().strip()
            if not q_clean or q_clean in seen:
                continue
            seen.add(q_clean)
            formatted = "".join(cls.MONOSPACE_DIGITS.get(c, cls.SMALL_CAPS_MAP.get(c, c)) for c in q_clean)
            parts.append(formatted)
        return f"‖ {' ‖ '.join(parts)} ‖" if parts else "‖ 𝟷𝟶𝟾𝟶ᴘ ‖"

    @classmethod
    def format_shadow_description(cls, item, fmt_string: Optional[str] = None) -> str:
        """Format the shadow database channel and batch bot delivery description text (e.g., Title, Year, Quality, Audio)."""
        fmt = fmt_string or settings.PRESENTATION_FORMAT
        raw_title = getattr(item, "parsed_title", None) or "Unknown Title"
        clean_title_compact = "".join(
            word.capitalize() for word in re.sub(r"[^\w\s]", "", raw_title).split()
        ) or "Unknown"

        raw_quality = getattr(item, "quality_tag", None) or "1080p"
        quality_clean = raw_quality.split()[0] if raw_quality else "1080p"

        raw_codec = getattr(item, "codec_tag", None) or "x265"
        codec_clean = (
            str(raw_codec).lower().replace("hevc", "265").replace("h.265", "265").replace("h.264", "264").replace("x", "")
        ) or "265"

        name_haystack = f"{getattr(item, 'clean_file_name', '')} {raw_title}".lower()
        audio_tag = "Eng"
        audio_full = "English"
        if "hindi" in name_haystack and "eng" in name_haystack:
            audio_tag = "Dual"
            audio_full = "Hindi - English"
        elif "hindi" in name_haystack:
            audio_tag = "Hin"
            audio_full = "Hindi"
        elif "tam" in name_haystack:
            audio_tag = "Tam"
            audio_full = "Tamil"
        elif "tel" in name_haystack:
            audio_tag = "Tel"
            audio_full = "Telugu"

        format_kwargs = {
            "title": raw_title,
            "raw_title": raw_title,
            "clean_title": clean_title_compact,
            "year": getattr(item, "year", None) or "2025",
            "quality": quality_clean,
            "raw_quality": raw_quality,
            "codec": codec_clean,
            "audio": audio_full,
            "audio_tag": audio_tag,
        }

        try:
            return fmt.format(**format_kwargs)
        except Exception:
            return (
                f"🍿 Title: {raw_title}\n"
                f"📆 Year: 2025\n"
                f"📦 Quality: {quality_clean}\n"
                f"🔊 Audio : {audio_full}\n"
                f"💬 English Subtitles 👍"
            )

    @classmethod
    def format_presentation(cls, item, fmt_string: Optional[str] = None) -> str:
        """Alias for format_shadow_description for backward compatibility."""
        return cls.format_shadow_description(item, fmt_string=fmt_string)

    @classmethod
    def format_rtg_message(
        cls, item, fmt_string: Optional[str] = None, batch_items: Optional[list] = None
    ) -> str:
        """Format the RTG presentation message with small-caps styling and channel template."""
        fmt = fmt_string or settings.RTG_MESSAGE_FORMAT
        raw_title = getattr(item, "parsed_title", None) or "Unknown Title"
        title_small_caps = cls.to_small_caps(raw_title)

        clean_title_compact = "".join(
            word.capitalize() for word in re.sub(r"[^\w\s]", "", raw_title).split()
        ) or "Unknown"

        # Year detection
        year_str = str(getattr(item, "year", None) or "")
        if not year_str or not year_str.isdigit():
            m_year = re.search(r"\b(19\d\d|20\d\d)\b", f"{getattr(item, 'clean_file_name', '')} {raw_title}")
            year_str = m_year.group(1) if m_year else "2025"

        # Season detection
        season_num = getattr(item, "season_num", None)
        if season_num is not None:
            season_line = f"📂 sᴇᴀsᴏɴ - {season_num}\n"
        else:
            season_line = ""

        # Audio detection
        name_haystack = f"{getattr(item, 'clean_file_name', '')} {raw_title}".lower()
        audio_tag = "Eng"
        audio_full = "English"
        if "hindi" in name_haystack and "eng" in name_haystack:
            audio_tag = "Dual"
            audio_full = "English + Hindi"
        elif "hindi" in name_haystack:
            audio_tag = "Hin"
            audio_full = "Hindi"
        elif "tam" in name_haystack:
            audio_tag = "Tam"
            audio_full = "Tamil"
        elif "tel" in name_haystack:
            audio_tag = "Tel"
            audio_full = "Telugu"

        audio_small_caps = cls.to_small_caps(audio_full)

        # Quality detection
        if batch_items:
            quals = [getattr(b, "quality_tag", "1080p") for b in batch_items if getattr(b, "quality_tag", None)]
            quality_stylized = cls.format_quality_stylized(quals)
        else:
            raw_quality = getattr(item, "quality_tag", None) or "1080p"
            quality_stylized = cls.format_quality_stylized([raw_quality])

        raw_quality = getattr(item, "quality_tag", None) or "1080p"
        quality_clean = raw_quality.split()[0] if raw_quality else "1080p"

        raw_codec = getattr(item, "codec_tag", None) or "x265"
        codec_clean = (
            str(raw_codec).lower().replace("hevc", "265").replace("h.265", "265").replace("h.264", "264").replace("x", "")
        ) or "265"

        format_kwargs = {
            "title": title_small_caps,
            "raw_title": raw_title,
            "clean_title": clean_title_compact,
            "year": year_str,
            "season": season_num if season_num is not None else 1,
            "season_line": season_line,
            "quality": quality_stylized,
            "raw_quality": raw_quality,
            "quality_clean": quality_clean,
            "codec": codec_clean,
            "audio": audio_small_caps,
            "raw_audio": audio_full,
            "audio_tag": audio_tag,
        }

        try:
            return fmt.format(**format_kwargs)
        except Exception:
            return (
                f"🎭 {title_small_caps} • {year_str}\n"
                f"{season_line}"
                f"🎧 ᴀᴜᴅɪᴏ - {audio_small_caps}\n"
                f"💬 sᴜʙᴛɪᴛʟᴇs 👍\n\n"
                f"🎞 ϙᴜᴀʟɪᴛʏ - {quality_stylized}\n\n"
                f"༺━━━━━━━━━━━━━━━༻\n"
                f"@TIF_TvSeries11🌹@TIF_WebSeries"
            )

