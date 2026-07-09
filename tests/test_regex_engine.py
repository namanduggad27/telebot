import pytest
from src.scrapers.regex_engine import RegexEngine


@pytest.mark.parametrize(
    "raw_filename,expected_title,expected_year,expected_season,expected_episode,expected_quality,expected_codec",
    [
        # Standard S01E01 scene releases
        ("Breaking.Bad.S01E01.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv", "Breaking Bad", None, 1, 1, "1080P WEB-DL", "H.264"),
        ("Silo.2023.S01E05.1080p.AMZN.WEB-DL.DDP5.1.H.264-GROUP.mkv", "Silo", 2023, 1, 5, "1080P AMZN WEB-DL", "H.264"),
        ("Game.of.Thrones.S08E06.720p.HDTV.x264-AVS.mp4", "Game of Thrones", None, 8, 6, "720P HDTV", "x264"),
        ("The.Last.of.Us.2023.S01E01.2160p.HMAX.WEB-DL.DDP5.1.Atmos.DV.MKV", "The Last of Us", 2023, 1, 1, "2160P HMAX WEB-DL", None),
        ("Stranger_Things_S04E09_1080p_NF_WEB-DL_x265.mkv", "Stranger Things", None, 4, 9, "1080P NF WEB-DL", "x265"),
        
        # Lowercase and compact numbering
        ("succession.s04e10.1080p.webrip.x264.mkv", "succession", None, 4, 10, "1080P WEBRIP", "x264"),
        ("Better.Call.Saul.S06E13.720p.AMZN.WEBRip.DDP5.1.x264-NTb.mkv", "Better Call Saul", None, 6, 13, "720P AMZN WEBRIP", "x264"),
        ("The.Mandalorian.2019.S03E08.1080p.DSNP.WEB-DL.DDP5.1.H.264-FLUX.mkv", "The Mandalorian", 2019, 3, 8, "1080P DSNP WEB-DL", "H.264"),
        ("Black.Mirror.S06E01.1080p.NF.WEB-DL.DDP5.1.Atmos.x264-FLUX.mkv", "Black Mirror", None, 6, 1, "1080P NF WEB-DL", "x264"),
        ("Loki.2021.S02E06.2160p.DSNP.WEB-DL.x265.10bit.HDR.DDP5.1.Atmos.mkv", "Loki", 2021, 2, 6, "2160P DSNP WEB-DL", "x265"),
        
        # 1x01 format
        ("House.of.the.Dragon.1x01.1080p.WEB-DL.x264.mkv", "House of the Dragon", None, 1, 1, "1080P WEB-DL", "x264"),
        ("Supernatural.15x20.720p.HDTV.x264-AVS.mkv", "Supernatural", None, 15, 20, "720P HDTV", "x264"),
        ("Doctor.Who.2005.13x06.1080p.WEB-DL.H264.mkv", "Doctor Who", 2005, 13, 6, "1080P WEB-DL", "H264"),
        
        # Episode only Ep01 / E01 / E1071
        ("One.Piece.E1071.1080p.WEB-DL.AAC.mkv", "One Piece", None, None, 1071, "1080P WEB-DL", None),
        ("Attack.on.Titan.Ep87.1080p.AMZN.WEB-DL.x264.mkv", "Attack on Titan", None, None, 87, "1080P AMZN WEB-DL", "x264"),
        
        # Season Packs
        ("Arcane.2021.S01.1080p.NF.WEB-DL.DDP5.1.x264-FLUX.mkv", "Arcane", 2021, 1, None, "1080P NF WEB-DL", "x264"),
        ("True.Detective.Season.1.1080p.BluRay.x264.mkv", "True Detective", None, 1, None, "1080P BLURAY", "x264"),
        
        # More complex scene strings
        ("Severance.2022.S01E09.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Severance", 2022, 1, 9, "1080P WEB-DL", "H.264"),
        ("Ted.Lasso.S03E12.2160p.WEB-DL.HEVC.Atmos.mkv", "Ted Lasso", None, 3, 12, "2160P WEB-DL", "HEVC"),
        ("The.Bear.2022.S02E06.1080p.WEB-DL.DDP5.1.x264-NTb.mkv", "The Bear", 2022, 2, 6, "1080P WEB-DL", "x264"),
        ("Fargo.S05E01.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv", "Fargo", None, 5, 1, "1080P WEB-DL", "H.264"),
        ("True.Detective.2014.S04E06.1080p.HMAX.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "True Detective", 2014, 4, 6, "1080P HMAX WEB-DL", "H.264"),
        ("The.Boys.2019.S04E01.1080p.AMZN.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "The Boys", 2019, 4, 1, "1080P AMZN WEB-DL", "H.264"),
        ("Fallout.2024.S01E08.2160p.AMZN.WEB-DL.DDP5.1.Atmos.H.265-FLUX.mkv", "Fallout", 2024, 1, 8, "2160P AMZN WEB-DL", "H.265"),
        ("Shogun.2024.S01E10.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv", "Shogun", 2024, 1, 10, "1080P WEB-DL", "H.264"),
        ("The.Acolyte.2024.S01E05.1080p.DSNP.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "The Acolyte", 2024, 1, 5, "1080P DSNP WEB-DL", "H.264"),
        ("House.of.the.Dragon.2022.S02E04.1080p.HMAX.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "House of the Dragon", 2022, 2, 4, "1080P HMAX WEB-DL", "H.264"),
        
        # Varied spacing and hyphens
        ("Peaky Blinders S06E06 1080p WEBRip x264.mp4", "Peaky Blinders", None, 6, 6, "1080P WEBRIP", "x264"),
        ("Sherlock - S04E03 - 1080p BluRay x264.mkv", "Sherlock", None, 4, 3, "1080P BLURAY", "x264"),
        ("Westworld.S04E08.1080p.WEB-DL.x265.mkv", "Westworld", None, 4, 8, "1080P WEB-DL", "x265"),
        ("Chernobyl.2019.S01E05.1080p.BluRay.x264.mkv", "Chernobyl", 2019, 1, 5, "1080P BLURAY", "x264"),
        ("Mr.Robot.S04E13.720p.WEB-DL.x264.mkv", "Mr Robot", None, 4, 13, "720P WEB-DL", "x264"),
        ("Invincible.2021.S02E08.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv", "Invincible", 2021, 2, 8, "1080P AMZN WEB-DL", "H.264"),
        ("Reacher.2022.S02E08.1080p.AMZN.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Reacher", 2022, 2, 8, "1080P AMZN WEB-DL", "H.264"),
        ("Monarch.Legacy.of.Monsters.2023.S01E10.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Monarch Legacy of Monsters", 2023, 1, 10, "1080P WEB-DL", "H.264"),
        ("Gen.V.2023.S01E08.1080p.AMZN.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Gen V", 2023, 1, 8, "1080P AMZN WEB-DL", "H.264"),
        ("Ahsoka.2023.S01E08.1080p.DSNP.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Ahsoka", 2023, 1, 8, "1080P DSNP WEB-DL", "H.264"),
        ("Lupin.2021.S03E07.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Lupin", 2021, 3, 7, "1080P NF WEB-DL", "H.264"),
        ("The.Crown.2016.S06E10.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "The Crown", 2016, 6, 10, "1080P NF WEB-DL", "H.264"),
        ("Squid.Game.2021.S01E09.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Squid Game", 2021, 1, 9, "1080P NF WEB-DL", "H.264"),
        ("Ozark.S04E14.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Ozark", None, 4, 14, "1080P NF WEB-DL", "H.264"),
        ("Dark.2017.S03E08.1080p.NF.WEB-DL.DDP5.1.x264-NTb.mkv", "Dark", 2017, 3, 8, "1080P NF WEB-DL", "x264"),
        ("Mindhunter.S02E09.1080p.NF.WEB-DL.DDP5.1.Atmos.x264-NTb.mkv", "Mindhunter", None, 2, 9, "1080P NF WEB-DL", "x264"),
        ("The.Witcher.2019.S03E08.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "The Witcher", 2019, 3, 8, "1080P NF WEB-DL", "H.264"),
        ("Bridgerton.2020.S03E08.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Bridgerton", 2020, 3, 8, "1080P NF WEB-DL", "H.264"),
        ("Cobra.Kai.2018.S06E05.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Cobra Kai", 2018, 6, 5, "1080P NF WEB-DL", "H.264"),
        ("The.Umbrella.Academy.2019.S04E06.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "The Umbrella Academy", 2019, 4, 6, "1080P NF WEB-DL", "H.264"),
        ("Avatar.The.Last.Airbender.2024.S01E08.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Avatar The Last Airbender", 2024, 1, 8, "1080P NF WEB-DL", "H.264"),
        ("3.Body.Problem.2024.S01E08.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "3 Body Problem", 2024, 1, 8, "1080P NF WEB-DL", "H.264"),
        ("Ripley.2024.S01E08.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Ripley", 2024, 1, 8, "1080P NF WEB-DL", "H.264"),
        ("Baby.Reindeer.2024.S01E07.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Baby Reindeer", 2024, 1, 7, "1080P NF WEB-DL", "H.264"),
        ("The.Gentlemen.2024.S01E08.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "The Gentlemen", 2024, 1, 8, "1080P NF WEB-DL", "H.264"),
        ("Masters.of.the.Air.2024.S01E09.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv", "Masters of the Air", 2024, 1, 9, "1080P WEB-DL", "H.264"),
    ],
)
def test_regex_parsing_accuracy(
    raw_filename,
    expected_title,
    expected_year,
    expected_season,
    expected_episode,
    expected_quality,
    expected_codec,
):
    """Verify that RegexEngine extracts precise titles, seasons, and episodes across 50+ real-world scene titles."""
    parsed = RegexEngine.parse(raw_filename)
    assert parsed.clean_title.lower() == expected_title.lower()
    assert parsed.year == expected_year
    assert parsed.season_num == expected_season
    assert parsed.episode_num == expected_episode
    assert parsed.quality == expected_quality
    assert parsed.codec == expected_codec


def test_season_pack_detection():
    """Verify season pack identification when no episode is present."""
    parsed = RegexEngine.parse("Arcane.2021.S01.1080p.NF.WEB-DL.DDP5.1.x264-FLUX.mkv")
    assert parsed.is_season_pack is True
    assert parsed.season_num == 1
    assert parsed.episode_num is None


def test_suggested_clean_filename():
    """Verify standardized filename generation."""
    parsed = RegexEngine.parse("Silo.2023.S01E05.1080p.AMZN.WEB-DL.DDP5.1.H.264-GROUP.mkv")
    assert parsed.clean_file_name == "Silo - (2023) - S01E05 - [1080P AMZN WEB-DL].mkv"
