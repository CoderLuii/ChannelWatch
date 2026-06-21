from types import SimpleNamespace
from typing import cast

from core.helpers.config import CoreSettings


def _settings() -> CoreSettings:
    return cast(
        CoreSettings,
        cast(
            object,
            SimpleNamespace(
                vod_cache_ttl=86400,
                vod_title=True,
                vod_episode_title=True,
                vod_summary=True,
                vod_duration=True,
                vod_progress=True,
                vod_image=True,
                vod_rating=True,
                vod_genres=True,
                vod_cast=True,
            ),
        ),
    )


def test_vod_metadata_provider_accepts_channels_uppercase_keys(monkeypatch):
    from core.helpers.vod_info import VODInfoProvider

    provider = VODInfoProvider("127.0.0.1", 8089, _settings())
    monkeypatch.setattr(
        provider,
        "_fetch_metadata",
        lambda: [
            {
                "ID": "123",
                "Title": "Example Movie",
                "EpisodeTitle": "Pilot",
                "Summary": "A summary",
                "Duration": 3600,
                "Image": "https://example.invalid/movie.jpg",
                "ContentRating": "TV-PG",
                "Genres": ["Drama"],
                "Cast": ["Actor One"],
            }
        ],
    )

    metadata = provider.get_metadata("123")
    formatted = provider.format_metadata(metadata or {}, "1m2s")

    assert metadata is not None
    assert provider.metadata_cache["123"] is metadata
    assert formatted == {
        "title": "Example Movie",
        "episode_title": "Pilot",
        "summary": "A summary",
        "duration": "1h 00m 00s",
        "progress": "1m2s",
        "image_url": "https://example.invalid/movie.jpg",
        "rating": "TV-PG",
        "genres": ["Drama"],
        "cast": ["Actor One"],
    }


def test_vod_metadata_provider_keeps_lowercase_key_compatibility(monkeypatch):
    from core.helpers.vod_info import VODInfoProvider

    provider = VODInfoProvider("127.0.0.1", 8089, _settings())
    monkeypatch.setattr(
        provider,
        "_fetch_metadata",
        lambda: [
            {
                "id": "abc",
                "title": "Lowercase Movie",
                "duration": 65,
                "image_url": "https://example.invalid/lower.jpg",
            }
        ],
    )

    metadata = provider.get_metadata("abc")
    formatted = provider.format_metadata(metadata or {})

    assert metadata is not None
    assert formatted["title"] == "Lowercase Movie"
    assert formatted["duration"] == "1m 05s"
    assert formatted["image_url"] == "https://example.invalid/lower.jpg"
