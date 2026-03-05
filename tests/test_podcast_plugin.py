"""
Tests for the Podcast plugin.

Tests cover:
- Feed parser (RSS parsing, duration conversion, date parsing, HTML stripping)
- PodcastStore (subscriptions, resume positions, recently played, persistence)
- PodcastProvider ContentProvider implementation
- JSON-RPC command dispatch (podcast items/search/play/addshow/delshow)
- Jive menu item format (episodes, feeds, recent items)
- CLI item format
- Parameter parsing helpers
- Error handling (missing params, corrupt data, fetch failures)
- Plugin lifecycle (setup/teardown)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.podcast.feed_parser import (
    PodcastEpisode,
    PodcastFeed,
    format_duration,
    parse_duration,
    parse_feed,
    parse_pub_date,
    strip_html,
)
from plugins.podcast.store import (
    DEFAULT_MAX_RECENT,
    DEFAULT_RESUME_THRESHOLD,
    EpisodeProgress,
    PodcastStore,
    RecentEpisode,
    Subscription,
)

# =============================================================================
# Feed parser — duration parsing
# =============================================================================


class TestParseDuration:
    def test_empty_string(self):
        assert parse_duration("") == 0

    def test_none_like(self):
        assert parse_duration("  ") == 0

    def test_plain_seconds(self):
        assert parse_duration("3661") == 3661

    def test_mm_ss(self):
        assert parse_duration("54:23") == 54 * 60 + 23

    def test_h_mm_ss(self):
        assert parse_duration("1:02:03") == 3600 + 120 + 3

    def test_hh_mm_ss(self):
        assert parse_duration("01:02:03") == 3600 + 120 + 3

    def test_hh_mm_ss_leading_zero_strip(self):
        # LMS strips leading "00:" — "00:54:23" → 54:23
        assert parse_duration("00:54:23") == 54 * 60 + 23

    def test_human_readable_hours(self):
        assert parse_duration("1h 2m 3s") == 3600 + 120 + 3

    def test_human_readable_minutes_only(self):
        assert parse_duration("45m") == 2700

    def test_human_readable_compact(self):
        assert parse_duration("1h30m") == 5400

    def test_float_seconds(self):
        assert parse_duration("123.456") == 123

    def test_invalid_returns_zero(self):
        assert parse_duration("not a duration") == 0

    def test_zero(self):
        assert parse_duration("0") == 0

    def test_zero_colon(self):
        assert parse_duration("0:00") == 0

    def test_large_value(self):
        assert parse_duration("10:00:00") == 36000


# =============================================================================
# Feed parser — date parsing
# =============================================================================


class TestParsePubDate:
    def test_rfc2822(self):
        iso, epoch = parse_pub_date("Thu, 01 Feb 2024 12:00:00 +0000")
        assert "2024-02-01" in iso
        assert epoch > 0

    def test_rfc2822_with_timezone(self):
        iso, epoch = parse_pub_date("Mon, 15 Jan 2024 08:30:00 -0500")
        assert "2024-01-15" in iso
        assert epoch > 0

    def test_iso8601(self):
        iso, epoch = parse_pub_date("2024-06-15T10:00:00+00:00")
        assert "2024-06-15" in iso
        assert epoch > 0

    def test_date_only(self):
        iso, epoch = parse_pub_date("2024-01-15")
        assert "2024-01-15" in iso
        assert epoch > 0

    def test_empty(self):
        iso, epoch = parse_pub_date("")
        assert iso == ""
        assert epoch == 0.0

    def test_invalid(self):
        iso, epoch = parse_pub_date("not a date")
        assert iso == ""
        assert epoch == 0.0

    def test_whitespace(self):
        iso, epoch = parse_pub_date("   ")
        assert iso == ""
        assert epoch == 0.0


# =============================================================================
# Feed parser — HTML stripping
# =============================================================================


class TestStripHtml:
    def test_plain_text(self):
        assert strip_html("Hello World") == "Hello World"

    def test_simple_tags(self):
        assert strip_html("<p>Hello</p> <b>World</b>") == "Hello World"

    def test_nested_tags(self):
        assert strip_html("<div><p>Hello <strong>World</strong></p></div>") == "Hello World"

    def test_whitespace_collapse(self):
        assert strip_html("Hello    \n   World") == "Hello World"

    def test_empty(self):
        assert strip_html("") == ""

    def test_none_like(self):
        assert strip_html("") == ""

    def test_entities_preserved(self):
        # We don't decode HTML entities — they pass through
        result = strip_html("<p>Hello &amp; World</p>")
        assert "Hello" in result
        assert "World" in result


# =============================================================================
# Feed parser — format_duration
# =============================================================================


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == ""

    def test_negative(self):
        assert format_duration(-5) == ""

    def test_seconds_only(self):
        assert format_duration(45) == "0:45"

    def test_minutes_seconds(self):
        assert format_duration(65) == "1:05"

    def test_hours(self):
        assert format_duration(3661) == "1:01:01"

    def test_exact_hour(self):
        assert format_duration(3600) == "1:00:00"

    def test_exact_minute(self):
        assert format_duration(60) == "1:00"

    def test_large(self):
        assert format_duration(36000) == "10:00:00"


# =============================================================================
# Feed parser — RSS parsing
# =============================================================================

_MINIMAL_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Test Podcast</title>
  <description>A test podcast</description>
  <link>https://example.com</link>
  <language>en</language>
  <itunes:author>Test Author</itunes:author>
  <itunes:image href="https://example.com/cover.jpg"/>
  <itunes:category text="Technology"/>
  <item>
    <title>Episode 2</title>
    <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg" length="12345678"/>
    <guid>ep2-guid</guid>
    <pubDate>Thu, 15 Feb 2024 12:00:00 +0000</pubDate>
    <itunes:duration>1:30:00</itunes:duration>
    <itunes:summary>Episode 2 description</itunes:summary>
    <itunes:image href="https://example.com/ep2.jpg"/>
    <itunes:episode>2</itunes:episode>
    <itunes:season>1</itunes:season>
  </item>
  <item>
    <title>Episode 1</title>
    <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" length="9876543"/>
    <guid>ep1-guid</guid>
    <pubDate>Mon, 01 Jan 2024 08:00:00 +0000</pubDate>
    <itunes:duration>45:30</itunes:duration>
    <description>Episode 1 description with &lt;b&gt;HTML&lt;/b&gt;</description>
    <itunes:episode>1</itunes:episode>
    <itunes:season>1</itunes:season>
  </item>
  <item>
    <title>No Audio Item</title>
    <description>This item has no enclosure</description>
    <guid>no-audio-guid</guid>
  </item>
</channel>
</rss>
"""


class TestParseFeed:
    def test_channel_metadata(self):
        feed = parse_feed(_MINIMAL_RSS, feed_url="https://example.com/feed.xml")
        assert feed.title == "Test Podcast"
        assert feed.description == "A test podcast"
        assert feed.author == "Test Author"
        assert feed.language == "en"
        assert feed.link == "https://example.com"
        assert feed.image_url == "https://example.com/cover.jpg"
        assert feed.url == "https://example.com/feed.xml"
        assert "Technology" in feed.categories

    def test_episodes_count(self):
        feed = parse_feed(_MINIMAL_RSS)
        # The "No Audio Item" should be filtered out (no enclosure)
        assert len(feed.episodes) == 2

    def test_episodes_sorted_newest_first(self):
        feed = parse_feed(_MINIMAL_RSS)
        assert feed.episodes[0].title == "Episode 2"
        assert feed.episodes[1].title == "Episode 1"

    def test_episode_fields(self):
        feed = parse_feed(_MINIMAL_RSS)
        ep2 = feed.episodes[0]
        assert ep2.title == "Episode 2"
        assert ep2.url == "https://example.com/ep2.mp3"
        assert ep2.guid == "ep2-guid"
        assert ep2.content_type == "audio/mpeg"
        assert ep2.file_size == 12345678
        assert ep2.duration_seconds == 5400  # 1:30:00
        assert ep2.description == "Episode 2 description"
        assert ep2.image_url == "https://example.com/ep2.jpg"
        assert ep2.episode_number == 2
        assert ep2.season_number == 1
        assert "2024-02-15" in ep2.published
        assert ep2.published_epoch > 0

    def test_episode_duration_mm_ss(self):
        feed = parse_feed(_MINIMAL_RSS)
        ep1 = feed.episodes[1]
        assert ep1.duration_seconds == 2730  # 45:30

    def test_episode_html_stripped_from_description(self):
        feed = parse_feed(_MINIMAL_RSS)
        ep1 = feed.episodes[1]
        # Description had HTML entities for <b>HTML</b>
        assert "<b>" not in ep1.description
        assert "<" not in ep1.description or "&lt;" in ep1.description

    def test_episode_fallback_image_to_feed(self):
        feed = parse_feed(_MINIMAL_RSS)
        ep1 = feed.episodes[1]
        # ep1 has no per-episode itunes:image, should fall back to feed image
        assert ep1.image_url == "https://example.com/cover.jpg"

    def test_no_channel_raises(self):
        xml = '<?xml version="1.0"?><notarss></notarss>'
        with pytest.raises(ValueError, match="No <channel>"):
            parse_feed(xml)

    def test_malformed_xml_raises(self):
        with pytest.raises(Exception):
            parse_feed("<not valid xml>>>")

    def test_empty_feed(self):
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Empty Show</title>
</channel>
</rss>
"""
        feed = parse_feed(xml)
        assert feed.title == "Empty Show"
        assert len(feed.episodes) == 0

    def test_episode_without_guid_uses_url(self):
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Test</title>
  <item>
    <title>No GUID</title>
    <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
  </item>
</channel>
</rss>
"""
        feed = parse_feed(xml)
        assert feed.episodes[0].guid == "https://example.com/ep.mp3"

    def test_enclosure_without_url_skipped(self):
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Test</title>
  <item>
    <title>Bad Enclosure</title>
    <enclosure type="audio/mpeg"/>
  </item>
</channel>
</rss>
"""
        feed = parse_feed(xml)
        assert len(feed.episodes) == 0

    def test_untitled_podcast_fallback(self):
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
<channel></channel>
</rss>
"""
        feed = parse_feed(xml)
        assert feed.title == "Untitled Podcast"

    def test_itunes_category_with_subcategory(self):
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Test</title>
  <itunes:category text="Society &amp; Culture">
    <itunes:category text="History"/>
  </itunes:category>
</channel>
</rss>
"""
        feed = parse_feed(xml)
        assert "Society & Culture" in feed.categories
        assert "Society & Culture > History" in feed.categories

    def test_explicit_flag(self):
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Explicit Show</title>
  <itunes:explicit>yes</itunes:explicit>
  <item>
    <title>Clean Episode</title>
    <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
    <itunes:explicit>no</itunes:explicit>
  </item>
</channel>
</rss>
"""
        feed = parse_feed(xml)
        assert feed.explicit is True
        assert feed.episodes[0].explicit is False

    def test_description_truncation(self):
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Test</title>
  <item>
    <title>Long Description</title>
    <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
    <description>{desc}</description>
  </item>
</channel>
</rss>
""".format(desc="A" * 3000)
        feed = parse_feed(xml)
        assert len(feed.episodes[0].description) <= 2003  # 2000 + "..."

    def test_standard_rss_image(self):
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Test</title>
  <image>
    <url>https://example.com/standard-image.jpg</url>
  </image>
</channel>
</rss>
"""
        feed = parse_feed(xml)
        assert feed.image_url == "https://example.com/standard-image.jpg"


# =============================================================================
# Feed parser — PodcastEpisode dataclass
# =============================================================================


class TestPodcastEpisode:
    def test_defaults(self):
        ep = PodcastEpisode(title="Test", url="https://example.com/ep.mp3")
        assert ep.title == "Test"
        assert ep.url == "https://example.com/ep.mp3"
        assert ep.guid == ""
        assert ep.description == ""
        assert ep.published == ""
        assert ep.published_epoch == 0.0
        assert ep.duration_seconds == 0
        assert ep.content_type == "audio/mpeg"
        assert ep.file_size == 0
        assert ep.image_url == ""
        assert ep.explicit is False
        assert ep.episode_number == 0
        assert ep.season_number == 0

    def test_frozen(self):
        ep = PodcastEpisode(title="Test", url="https://example.com/ep.mp3")
        with pytest.raises(AttributeError):
            ep.title = "Changed"  # type: ignore[misc]


# =============================================================================
# Feed parser — PodcastFeed dataclass
# =============================================================================


class TestPodcastFeed:
    def test_defaults(self):
        feed = PodcastFeed(title="Test", url="https://example.com/feed.xml")
        assert feed.title == "Test"
        assert feed.url == "https://example.com/feed.xml"
        assert feed.description == ""
        assert feed.author == ""
        assert feed.language == ""
        assert feed.image_url == ""
        assert feed.link == ""
        assert feed.categories == []
        assert feed.explicit is False
        assert feed.episodes == []

    def test_frozen(self):
        feed = PodcastFeed(title="Test", url="https://example.com/feed.xml")
        with pytest.raises(AttributeError):
            feed.title = "Changed"  # type: ignore[misc]


# =============================================================================
# Store — Subscription dataclass
# =============================================================================


class TestSubscription:
    def test_to_dict_minimal(self):
        sub = Subscription(name="Test", url="https://example.com/feed.xml")
        d = sub.to_dict()
        assert d["name"] == "Test"
        assert d["url"] == "https://example.com/feed.xml"
        assert "image" not in d  # empty fields omitted

    def test_to_dict_full(self):
        sub = Subscription(
            name="Test",
            url="https://example.com/feed.xml",
            image="https://example.com/cover.jpg",
            author="Author",
            description="Desc",
            added_at=1000.0,
        )
        d = sub.to_dict()
        assert d["image"] == "https://example.com/cover.jpg"
        assert d["author"] == "Author"
        assert d["description"] == "Desc"
        assert d["added_at"] == 1000.0

    def test_from_dict(self):
        d = {"name": "Test", "url": "https://example.com/feed.xml", "image": "img.jpg"}
        sub = Subscription.from_dict(d)
        assert sub.name == "Test"
        assert sub.url == "https://example.com/feed.xml"
        assert sub.image == "img.jpg"

    def test_from_dict_missing_fields(self):
        d = {"name": "Test", "url": "https://example.com/feed.xml"}
        sub = Subscription.from_dict(d)
        assert sub.image == ""
        assert sub.author == ""


# =============================================================================
# Store — RecentEpisode dataclass
# =============================================================================


class TestRecentEpisode:
    def test_to_dict_minimal(self):
        ep = RecentEpisode(url="https://example.com/ep.mp3")
        d = ep.to_dict()
        assert d["url"] == "https://example.com/ep.mp3"
        assert "title" not in d

    def test_to_dict_full(self):
        ep = RecentEpisode(
            url="https://example.com/ep.mp3",
            title="Episode 1",
            show="My Podcast",
            image="cover.jpg",
            duration=3600,
            feed_url="https://example.com/feed.xml",
            played_at=1000.0,
        )
        d = ep.to_dict()
        assert d["title"] == "Episode 1"
        assert d["show"] == "My Podcast"
        assert d["duration"] == 3600
        assert d["feed_url"] == "https://example.com/feed.xml"

    def test_from_dict(self):
        d = {"url": "https://example.com/ep.mp3", "title": "Ep 1", "duration": 300}
        ep = RecentEpisode.from_dict(d)
        assert ep.url == "https://example.com/ep.mp3"
        assert ep.title == "Ep 1"
        assert ep.duration == 300


# =============================================================================
# Store — PodcastStore
# =============================================================================


class TestPodcastStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> PodcastStore:
        return PodcastStore(tmp_path)

    def test_empty_store(self, store: PodcastStore):
        assert store.subscription_count == 0
        assert store.recent_count == 0
        assert store.subscriptions == []
        assert store.recent == []

    # -- Subscriptions -------------------------------------------------------

    def test_add_subscription(self, store: PodcastStore):
        result = store.add_subscription(
            url="https://example.com/feed.xml",
            name="Test Podcast",
            image="cover.jpg",
        )
        assert result is True
        assert store.subscription_count == 1
        assert store.is_subscribed("https://example.com/feed.xml")

    def test_add_duplicate_subscription(self, store: PodcastStore):
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        result = store.add_subscription(url="https://example.com/feed.xml", name="Test")
        assert result is False
        assert store.subscription_count == 1

    def test_remove_subscription(self, store: PodcastStore):
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        result = store.remove_subscription("https://example.com/feed.xml")
        assert result is True
        assert store.subscription_count == 0
        assert not store.is_subscribed("https://example.com/feed.xml")

    def test_remove_nonexistent_subscription(self, store: PodcastStore):
        result = store.remove_subscription("https://example.com/nope.xml")
        assert result is False

    def test_get_subscription(self, store: PodcastStore):
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        sub = store.get_subscription("https://example.com/feed.xml")
        assert sub is not None
        assert sub.name == "Test"

    def test_get_nonexistent_subscription(self, store: PodcastStore):
        assert store.get_subscription("https://example.com/nope.xml") is None

    def test_update_subscription(self, store: PodcastStore):
        store.add_subscription(url="https://example.com/feed.xml", name="Old Name")
        result = store.update_subscription(
            "https://example.com/feed.xml",
            name="New Name",
            image="new_cover.jpg",
        )
        assert result is True
        sub = store.get_subscription("https://example.com/feed.xml")
        assert sub is not None
        assert sub.name == "New Name"
        assert sub.image == "new_cover.jpg"

    def test_update_subscription_partial(self, store: PodcastStore):
        store.add_subscription(
            url="https://example.com/feed.xml",
            name="Test",
            image="old.jpg",
        )
        store.update_subscription("https://example.com/feed.xml", name="Updated")
        sub = store.get_subscription("https://example.com/feed.xml")
        assert sub is not None
        assert sub.name == "Updated"
        assert sub.image == "old.jpg"  # not changed

    def test_update_nonexistent_subscription(self, store: PodcastStore):
        result = store.update_subscription("https://example.com/nope.xml", name="X")
        assert result is False

    def test_subscription_order_preserved(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="Alpha")
        store.add_subscription(url="https://b.com/feed.xml", name="Beta")
        store.add_subscription(url="https://c.com/feed.xml", name="Gamma")
        subs = store.subscriptions
        assert [s.name for s in subs] == ["Alpha", "Beta", "Gamma"]

    def test_subscription_added_at_timestamp(self, store: PodcastStore):
        before = time.time()
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        after = time.time()
        sub = store.get_subscription("https://example.com/feed.xml")
        assert sub is not None
        assert before <= sub.added_at <= after

    # -- Resume positions ----------------------------------------------------

    def test_resume_position_default(self, store: PodcastStore):
        assert store.get_resume_position("https://example.com/ep.mp3") == 0

    def test_set_resume_position(self, store: PodcastStore):
        store.set_resume_position("https://example.com/ep.mp3", 300)
        assert store.get_resume_position("https://example.com/ep.mp3") == 300

    def test_resume_position_below_threshold_cleared(self, store: PodcastStore):
        store.set_resume_position("https://example.com/ep.mp3", 300)
        # Position below threshold → cleared
        store.set_resume_position("https://example.com/ep.mp3", 5)
        assert store.get_resume_position("https://example.com/ep.mp3") == 0

    def test_resume_position_near_end_cleared(self, store: PodcastStore):
        store.set_resume_position("https://example.com/ep.mp3", 300)
        # Position near end of episode → cleared (finished)
        store.set_resume_position("https://example.com/ep.mp3", 3590, duration=3600)
        assert store.get_resume_position("https://example.com/ep.mp3") == 0

    def test_resume_position_near_end_no_duration(self, store: PodcastStore):
        # Without duration, near-end logic doesn't apply
        store.set_resume_position("https://example.com/ep.mp3", 3590, duration=0)
        assert store.get_resume_position("https://example.com/ep.mp3") == 3590

    def test_clear_resume_position(self, store: PodcastStore):
        store.set_resume_position("https://example.com/ep.mp3", 300)
        store.clear_resume_position("https://example.com/ep.mp3")
        assert store.get_resume_position("https://example.com/ep.mp3") == 0

    def test_has_resume_position(self, store: PodcastStore):
        assert not store.has_resume_position("https://example.com/ep.mp3")
        store.set_resume_position("https://example.com/ep.mp3", 300)
        assert store.has_resume_position("https://example.com/ep.mp3")

    def test_resume_positions_property(self, store: PodcastStore):
        store.set_resume_position("https://example.com/ep1.mp3", 100)
        store.set_resume_position("https://example.com/ep2.mp3", 200)
        positions = store.resume_positions
        assert len(positions) == 2
        assert positions["https://example.com/ep1.mp3"] == 100
        assert positions["https://example.com/ep2.mp3"] == 200

    def test_resume_threshold_boundary(self, store: PodcastStore):
        # Exactly at threshold — should be cleared
        store.set_resume_position("https://example.com/ep.mp3", DEFAULT_RESUME_THRESHOLD - 1)
        assert store.get_resume_position("https://example.com/ep.mp3") == 0

        # Just above threshold — should be stored
        store.set_resume_position("https://example.com/ep.mp3", DEFAULT_RESUME_THRESHOLD)
        assert store.get_resume_position("https://example.com/ep.mp3") == DEFAULT_RESUME_THRESHOLD

    # -- Recently played -----------------------------------------------------

    def test_record_played(self, store: PodcastStore):
        store.record_played(
            url="https://example.com/ep.mp3",
            title="Episode 1",
            show="Test Podcast",
        )
        assert store.recent_count == 1
        assert store.recent[0].url == "https://example.com/ep.mp3"
        assert store.recent[0].title == "Episode 1"
        assert store.recent[0].show == "Test Podcast"

    def test_record_played_dedup(self, store: PodcastStore):
        store.record_played(url="https://example.com/ep.mp3", title="First")
        store.record_played(url="https://example.com/ep.mp3", title="Updated")
        assert store.recent_count == 1
        assert store.recent[0].title == "Updated"

    def test_record_played_newest_first(self, store: PodcastStore):
        store.record_played(url="https://example.com/ep1.mp3", title="Episode 1")
        store.record_played(url="https://example.com/ep2.mp3", title="Episode 2")
        assert store.recent[0].title == "Episode 2"
        assert store.recent[1].title == "Episode 1"

    def test_record_played_max_limit(self, store: PodcastStore):
        for i in range(DEFAULT_MAX_RECENT + 10):
            store.record_played(url=f"https://example.com/ep{i}.mp3", title=f"Ep {i}")
        assert store.recent_count == DEFAULT_MAX_RECENT

    def test_record_played_timestamp(self, store: PodcastStore):
        before = time.time()
        store.record_played(url="https://example.com/ep.mp3")
        after = time.time()
        assert before <= store.recent[0].played_at <= after

    def test_clear_recent(self, store: PodcastStore):
        store.record_played(url="https://example.com/ep.mp3")
        store.clear_recent()
        assert store.recent_count == 0

    # -- Persistence ---------------------------------------------------------

    def test_save_and_load(self, tmp_path: Path):
        store1 = PodcastStore(tmp_path)
        store1.add_subscription(url="https://example.com/feed.xml", name="Test")
        store1.set_resume_position("https://example.com/ep.mp3", 300)
        store1.record_played(url="https://example.com/ep.mp3", title="Ep 1")
        store1.save()

        store2 = PodcastStore(tmp_path)
        store2.load()
        assert store2.subscription_count == 1
        assert store2.is_subscribed("https://example.com/feed.xml")
        assert store2.get_resume_position("https://example.com/ep.mp3") == 300
        assert store2.recent_count == 1
        assert store2.recent[0].title == "Ep 1"

    def test_save_creates_directory(self, tmp_path: Path):
        store = PodcastStore(tmp_path / "nested" / "dir")
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        assert (tmp_path / "nested" / "dir" / "podcasts.json").is_file()

    def test_save_is_valid_json(self, tmp_path: Path):
        store = PodcastStore(tmp_path)
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        with open(tmp_path / "podcasts.json", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "subscriptions" in data
        assert "resume" in data
        assert "recent" in data

    def test_load_corrupt_json(self, tmp_path: Path):
        (tmp_path / "podcasts.json").write_text("not valid json!!!", encoding="utf-8")
        store = PodcastStore(tmp_path)
        store.load()
        # Should start empty without crashing
        assert store.subscription_count == 0

    def test_load_nonexistent(self, tmp_path: Path):
        store = PodcastStore(tmp_path)
        store.load()
        assert store.subscription_count == 0

    def test_load_wrong_type(self, tmp_path: Path):
        (tmp_path / "podcasts.json").write_text('"just a string"', encoding="utf-8")
        store = PodcastStore(tmp_path)
        store.load()
        assert store.subscription_count == 0

    def test_load_skips_invalid_subscriptions(self, tmp_path: Path):
        data = {
            "subscriptions": [
                {"name": "Good", "url": "https://example.com/feed.xml"},
                {"name": "Bad"},  # no url
                "not a dict",
            ],
            "resume": {},
            "recent": [],
        }
        (tmp_path / "podcasts.json").write_text(json.dumps(data), encoding="utf-8")
        store = PodcastStore(tmp_path)
        store.load()
        assert store.subscription_count == 1
        assert store.subscriptions[0].name == "Good"

    def test_load_skips_invalid_resume(self, tmp_path: Path):
        data = {
            "subscriptions": [],
            "resume": {"https://example.com/ep.mp3": 300, "bad": -5, "zero": 0},
            "recent": [],
        }
        (tmp_path / "podcasts.json").write_text(json.dumps(data), encoding="utf-8")
        store = PodcastStore(tmp_path)
        store.load()
        assert len(store.resume_positions) == 1
        assert store.get_resume_position("https://example.com/ep.mp3") == 300

    # -- Bulk operations -----------------------------------------------------

    def test_clear_all(self, store: PodcastStore):
        store.add_subscription(url="https://example.com/feed.xml", name="Test")
        store.set_resume_position("https://example.com/ep.mp3", 300)
        store.record_played(url="https://example.com/ep.mp3")
        store.clear_all()
        assert store.subscription_count == 0
        assert store.recent_count == 0
        assert store.get_resume_position("https://example.com/ep.mp3") == 0


# =============================================================================
# PodcastProvider — ContentProvider
# =============================================================================


class TestPodcastProvider:
    def _make_provider(self) -> Any:
        from plugins.podcast import PodcastProvider
        return PodcastProvider()

    def test_name(self):
        provider = self._make_provider()
        assert provider.name == "Podcasts"

    def test_icon(self):
        provider = self._make_provider()
        assert provider.icon is None

    @pytest.mark.asyncio
    async def test_get_stream_info_http_url(self):
        provider = self._make_provider()
        info = await provider.get_stream_info("https://example.com/episode.mp3")
        assert info is not None
        assert info.url == "https://example.com/episode.mp3"
        assert info.content_type == "audio/mpeg"
        assert info.is_live is False

    @pytest.mark.asyncio
    async def test_get_stream_info_non_url(self):
        provider = self._make_provider()
        info = await provider.get_stream_info("not-a-url")
        assert info is None

    @pytest.mark.asyncio
    async def test_browse_root_empty_store(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 0
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = []
            podcast_mod._store = mock_store

            provider = self._make_provider()
            items = await provider.browse("")
            # Should have at least the search item and trending
            assert any(item.type == "search" for item in items)
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_browse_root_with_subscriptions(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 1
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = [
                Subscription(name="My Podcast", url="https://example.com/feed.xml", image="img.jpg"),
            ]
            podcast_mod._store = mock_store

            provider = self._make_provider()
            items = await provider.browse("")

            # Filter to only subscription folders (exclude __whatsnew__, __trending__, etc.)
            sub_items = [i for i in items if i.type == "folder" and not i.id.startswith("__")]
            assert len(sub_items) == 1
            assert sub_items[0].title == "My Podcast"
            assert sub_items[0].url == "https://example.com/feed.xml"
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_browse_root_with_recent(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 3
            mock_store.subscription_count = 0
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = []
            podcast_mod._store = mock_store

            provider = self._make_provider()
            items = await provider.browse("")

            folder_items = [i for i in items if i.type == "folder" and i.id == "__recent__"]
            assert len(folder_items) == 1
            assert "3 episodes" in folder_items[0].subtitle
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_browse_no_store(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = None
            provider = self._make_provider()
            items = await provider.browse("")
            assert items == []
        finally:
            podcast_mod._store = old_store


# =============================================================================
# Parameter parsing helpers
# =============================================================================


class TestParameterParsing:
    def test_parse_tagged_colon_format(self):
        from plugins.podcast import _parse_tagged
        result = _parse_tagged(["podcast", "items", "url:https://example.com", "menu:1"], start=2)
        assert result["url"] == "https://example.com"
        assert result["menu"] == "1"

    def test_parse_tagged_dict_format(self):
        from plugins.podcast import _parse_tagged
        result = _parse_tagged(["podcast", "items", {"url": "https://example.com", "menu": "1"}], start=2)
        assert result["url"] == "https://example.com"
        assert result["menu"] == "1"

    def test_parse_tagged_mixed(self):
        from plugins.podcast import _parse_tagged
        result = _parse_tagged(["podcast", "items", "url:https://example.com", {"menu": "1"}], start=2)
        assert result["url"] == "https://example.com"
        assert result["menu"] == "1"

    def test_parse_tagged_ignores_non_tagged(self):
        from plugins.podcast import _parse_tagged
        result = _parse_tagged(["podcast", "items", "notatag", "key:val"], start=2)
        assert "notatag" not in result
        assert result["key"] == "val"

    def test_parse_tagged_none_values_skipped(self):
        from plugins.podcast import _parse_tagged
        result = _parse_tagged(["podcast", "items", {"key": None, "key2": "val"}], start=2)
        assert "key" not in result
        assert result["key2"] == "val"

    def test_parse_start_count_defaults(self):
        from plugins.podcast import _parse_start_count
        start, count = _parse_start_count(["podcast", "items"])
        assert start == 0
        assert count == 200

    def test_parse_start_count_explicit(self):
        from plugins.podcast import _parse_start_count
        start, count = _parse_start_count(["podcast", "items", 10, 50])
        assert start == 10
        assert count == 50

    def test_parse_start_count_negative_clamped(self):
        from plugins.podcast import _parse_start_count
        start, count = _parse_start_count(["podcast", "items", -5, 50])
        assert start == 0

    def test_parse_start_count_large_clamped(self):
        from plugins.podcast import _parse_start_count
        start, count = _parse_start_count(["podcast", "items", 0, 20_000])
        assert count == 10_000

    def test_parse_start_count_invalid_types(self):
        from plugins.podcast import _parse_start_count
        start, count = _parse_start_count(["podcast", "items", "invalid", "bad"])
        assert start == 0
        assert count == 200


# =============================================================================
# Jive menu item builders
# =============================================================================


class TestJiveMenuBuilders:
    def _make_episode(self, **kwargs: Any) -> PodcastEpisode:
        defaults = {
            "title": "Test Episode",
            "url": "https://example.com/ep.mp3",
            "guid": "ep-guid",
            "published": "2024-02-15T12:00:00+00:00",
            "duration_seconds": 3600,
            "content_type": "audio/mpeg",
            "image_url": "https://example.com/ep.jpg",
        }
        defaults.update(kwargs)
        return PodcastEpisode(**defaults)

    def test_build_jive_episode_item_basic(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_episode_item

            ep = self._make_episode()
            item = _build_jive_episode_item(ep, feed_url="https://example.com/feed.xml")

            assert item["text"] == "Test Episode"
            assert item["type"] == "audio"
            assert item["hasitems"] == 0
            assert "play" in item["actions"]
            assert "add" in item["actions"]
            assert "more" in item["actions"]
            assert item["actions"]["play"]["cmd"] == ["podcast", "play"]
            assert item["actions"]["play"]["params"]["url"] == "https://example.com/ep.mp3"
        finally:
            podcast_mod._store = old_store

    def test_build_jive_episode_item_with_icon(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_episode_item

            ep = self._make_episode(image_url="https://example.com/ep.jpg")
            item = _build_jive_episode_item(ep)
            assert item["icon"] == "https://example.com/ep.jpg"
        finally:
            podcast_mod._store = old_store

    def test_build_jive_episode_item_fallback_image(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_episode_item

            ep = self._make_episode(image_url="")
            item = _build_jive_episode_item(ep, feed_image="https://example.com/feed.jpg")
            assert item["icon"] == "https://example.com/feed.jpg"
        finally:
            podcast_mod._store = old_store

    def test_build_jive_episode_item_subtitle(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_episode_item

            ep = self._make_episode(
                published="2024-02-15T12:00:00+00:00",
                duration_seconds=3600,
            )
            item = _build_jive_episode_item(ep)
            assert "2024-02-15" in item.get("textkey", "")
            assert "1:00:00" in item.get("textkey", "")
        finally:
            podcast_mod._store = old_store

    def test_build_jive_episode_item_with_resume(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 600  # 10 minutes in
            mock_store.get_progress_percentage.return_value = 16.7
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_episode_item

            ep = self._make_episode(duration_seconds=3600)
            item = _build_jive_episode_item(ep)

            # With resume, type should be redirect to show sub-menu
            assert item["type"] == "redirect"
            assert item["hasitems"] == 1
            assert "10:00" in item.get("textkey", "")
        finally:
            podcast_mod._store = old_store

    def test_build_jive_episode_info_action(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_episode_item

            ep = self._make_episode()
            item = _build_jive_episode_item(ep)
            assert item["actions"]["more"]["cmd"] == ["podcast", "info"]
            assert item["actions"]["more"]["params"]["name"] == "Test Episode"
        finally:
            podcast_mod._store = old_store

    def test_build_jive_feed_item(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_feed_item

            feed_data = {
                "name": "Test Podcast",
                "url": "https://example.com/feed.xml",
                "image": "https://example.com/cover.jpg",
                "author": "Test Author",
            }
            item = _build_jive_feed_item(feed_data)
            assert item["text"] == "Test Podcast"
            assert item["hasitems"] == 1
            assert item["icon"] == "https://example.com/cover.jpg"
            assert "Test Author" in item.get("textkey", "")
            assert item["actions"]["go"]["cmd"] == ["podcast", "items"]
            assert item["actions"]["go"]["params"]["url"] == "https://example.com/feed.xml"
            # Info context menu
            assert item["actions"]["more"]["cmd"] == ["podcast", "info"]
        finally:
            podcast_mod._store = old_store

    def test_build_jive_feed_item_no_image(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_feed_item

            feed_data = {"name": "No Image", "url": "https://example.com/feed.xml"}
            item = _build_jive_feed_item(feed_data)
            assert "icon" not in item
        finally:
            podcast_mod._store = old_store

    def test_build_jive_recent_item(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_jive_recent_item

            ep = RecentEpisode(
                url="https://example.com/ep.mp3",
                title="Recent Episode",
                show="My Show",
                image="cover.jpg",
            )
            item = _build_jive_recent_item(ep)
            assert item["text"] == "Recent Episode"
            assert item["type"] == "audio"
            assert "My Show" in item.get("textkey", "")
            assert item["actions"]["play"]["params"]["url"] == "https://example.com/ep.mp3"
        finally:
            podcast_mod._store = old_store


# =============================================================================
# CLI item builders
# =============================================================================


class TestCLIItemBuilders:
    def test_build_cli_episode_item(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_cli_episode_item

            ep = PodcastEpisode(
                title="Test Episode",
                url="https://example.com/ep.mp3",
                content_type="audio/mpeg",
                duration_seconds=3600,
                published="2024-02-15T12:00:00+00:00",
                image_url="cover.jpg",
            )
            item = _build_cli_episode_item(ep, feed_url="https://example.com/feed.xml")
            assert item["name"] == "Test Episode"
            assert item["url"] == "https://example.com/ep.mp3"
            assert item["type"] == "audio"
            assert item["content_type"] == "audio/mpeg"
            assert item["duration"] == 3600
            assert item["duration_text"] == "1:00:00"
            assert item["published"] == "2024-02-15T12:00:00+00:00"
            assert item["feed_url"] == "https://example.com/feed.xml"
        finally:
            podcast_mod._store = old_store

    def test_build_cli_episode_item_with_resume_and_progress(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 300
            mock_store.get_progress_percentage.return_value = 8.3
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _build_cli_episode_item

            ep = PodcastEpisode(
                title="Test",
                url="https://example.com/ep.mp3",
            )
            item = _build_cli_episode_item(ep)
            assert item["resume_position"] == 300
        finally:
            podcast_mod._store = old_store

    def test_build_cli_item_from_search(self):
        from plugins.podcast import _build_cli_item_from_search

        data = {
            "name": "Found Podcast",
            "url": "https://example.com/feed.xml",
            "image": "cover.jpg",
            "author": "Author",
            "description": "A great podcast",
        }
        item = _build_cli_item_from_search(data)
        assert item["name"] == "Found Podcast"
        assert item["url"] == "https://example.com/feed.xml"
        assert item["type"] == "link"
        assert item["image"] == "cover.jpg"
        assert item["author"] == "Author"

    def test_build_cli_item_from_search_long_description_truncated(self):
        from plugins.podcast import _build_cli_item_from_search

        data = {
            "name": "Test",
            "url": "https://example.com/feed.xml",
            "description": "A" * 500,
        }
        item = _build_cli_item_from_search(data)
        assert len(item["description"]) == 200


# =============================================================================
# Base actions
# =============================================================================


class TestBaseActions:
    def test_base_actions_structure(self):
        from plugins.podcast import _base_actions

        base = _base_actions()
        assert "actions" in base
        assert "go" in base["actions"]
        assert "play" in base["actions"]
        assert "add" in base["actions"]
        assert base["actions"]["go"]["cmd"] == ["podcast", "items"]
        assert base["actions"]["play"]["cmd"] == ["podcast", "play"]
        assert base["actions"]["add"]["cmd"] == ["podcast", "play"]
        assert base["actions"]["add"]["params"]["cmd"] == "add"


# =============================================================================
# JSON-RPC command dispatch
# =============================================================================


class _FakeCommandContext:
    """Minimal CommandContext for testing."""

    def __init__(
        self,
        player_id: str = "aa:bb:cc:dd:ee:ff",
        *,
        player: Any = None,
        playlist: Any = None,
        playlist_manager: Any = None,
    ) -> None:
        self.player_id = player_id
        self.player_registry = AsyncMock()
        self.player_registry.get_by_mac = AsyncMock(return_value=player)
        self.playlist_manager = playlist_manager


class TestCmdPodcast:
    def _make_ctx(self) -> _FakeCommandContext:
        return _FakeCommandContext()

    @pytest.mark.asyncio
    async def test_dispatch_default_to_items(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 0
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = []
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            from plugins.podcast import cmd_podcast

            ctx = self._make_ctx()
            result = await cmd_podcast(ctx, ["podcast"])
            # Default sub-command is "items"
            assert "count" in result
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_dispatch_items(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 0
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = []
            podcast_mod._store = mock_store

            from plugins.podcast import cmd_podcast

            ctx = self._make_ctx()
            result = await cmd_podcast(ctx, ["podcast", "items"])
            assert "count" in result
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_dispatch_search(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.subscription_count = 0
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            from plugins.podcast import cmd_podcast

            ctx = self._make_ctx()
            # Empty search returns empty results
            result = await cmd_podcast(ctx, ["podcast", "search", 0, 100])
            assert "count" in result
            assert result["count"] == 0
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_dispatch_unknown(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.subscription_count = 0
            podcast_mod._store = mock_store

            from plugins.podcast import cmd_podcast

            ctx = self._make_ctx()
            result = await cmd_podcast(ctx, ["podcast", "unknown"])
            assert "error" in result
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_dispatch_not_initialized(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = None

            from plugins.podcast import cmd_podcast

            ctx = self._make_ctx()
            result = await cmd_podcast(ctx, ["podcast", "items"])
            assert "error" in result
            assert "not initialized" in result["error"]
        finally:
            podcast_mod._store = old_store


# =============================================================================
# podcast items — browse tests
# =============================================================================


class TestPodcastItems:
    def _make_ctx(self) -> _FakeCommandContext:
        return _FakeCommandContext()

    @pytest.mark.asyncio
    async def test_items_root_menu_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 2
            mock_store.subscription_count = 2
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = [
                Subscription(name="Podcast A", url="https://a.com/feed.xml", image="a.jpg"),
                Subscription(name="Podcast B", url="https://b.com/feed.xml"),
            ]
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_items

            ctx = self._make_ctx()
            result = await _podcast_items(ctx, ["podcast", "items", 0, 100, "menu:1"])

            assert "item_loop" in result
            items = result["item_loop"]
            # Should have: search + what's new + recently played + trending + 2 subscriptions = 6
            assert result["count"] == 6

            # First item: search
            assert "input" in items[0]

            # Second item: What's New
            assert "What's New" in items[1]["text"]

            # Third item: recently played
            assert items[2]["text"] == "Recently Played"

            # Fourth item: trending
            assert items[3]["text"] == "Trending Podcasts"

            # Subscriptions
            assert items[4]["text"] == "Podcast A"
            assert items[5]["text"] == "Podcast B"

            # Base actions
            assert "base" in result
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_items_root_cli_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 2
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = [
                Subscription(name="Test", url="https://example.com/feed.xml"),
            ]
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_items

            ctx = self._make_ctx()
            result = await _podcast_items(ctx, ["podcast", "items", 0, 100])

            assert "loop" in result
            assert result["count"] == 1
            assert result["loop"][0]["name"] == "Test"
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_items_feed_episodes(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_cache = podcast_mod._feed_cache.copy()
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            mock_store.is_subscribed.return_value = True
            podcast_mod._store = mock_store

            # Pre-populate feed cache
            feed = PodcastFeed(
                title="Test Podcast",
                url="https://example.com/feed.xml",
                image_url="cover.jpg",
                episodes=[
                    PodcastEpisode(
                        title="Episode 1",
                        url="https://example.com/ep1.mp3",
                        guid="ep1",
                        published="2024-01-01T00:00:00+00:00",
                        published_epoch=1704067200.0,
                        duration_seconds=1800,
                        content_type="audio/mpeg",
                    ),
                ],
            )
            podcast_mod._feed_cache["https://example.com/feed.xml"] = (feed, time.time() + 600)

            from plugins.podcast import _podcast_items

            ctx = self._make_ctx()
            result = await _podcast_items(ctx, [
                "podcast", "items", 0, 100,
                "url:https://example.com/feed.xml", "menu:1",
            ])

            assert result["count"] == 1
            items = result["item_loop"]
            assert items[0]["text"] == "Episode 1"
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)

    @pytest.mark.asyncio
    async def test_items_recently_played(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.subscription_count = 0
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            mock_store.recent = [
                RecentEpisode(
                    url="https://example.com/ep.mp3",
                    title="Recent Ep",
                    show="A Show",
                    image="img.jpg",
                ),
            ]
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_items

            ctx = self._make_ctx()
            result = await _podcast_items(ctx, [
                "podcast", "items", 0, 100,
                "url:__recent__", "menu:1",
            ])

            assert result["count"] == 1
            assert result["item_loop"][0]["text"] == "Recent Ep"
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_items_pagination(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 5
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = [
                Subscription(name=f"Sub {i}", url=f"https://example.com/feed{i}.xml")
                for i in range(5)
            ]
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_items

            ctx = self._make_ctx()
            result = await _podcast_items(ctx, ["podcast", "items", 3, 5, "menu:1"])

            # count is total: search + what's new + trending + 5 subs = 8
            assert result["count"] == 8
            assert result["offset"] == 3
            assert len(result["item_loop"]) == 5
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_items_empty(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.recent_count = 0
            mock_store.subscription_count = 0
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = []
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_items

            ctx = self._make_ctx()
            result = await _podcast_items(ctx, ["podcast", "items", 0, 100, "menu:1"])

            # Search + Trending = 2 items (no subscriptions → no What's New)
            assert result["count"] == 2
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_items_feed_fetch_error(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_cache = podcast_mod._feed_cache.copy()
        old_client = podcast_mod._http_client
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store
            podcast_mod._feed_cache.clear()
            # Mock client that fails
            podcast_mod._http_client = MagicMock()

            with patch("plugins.podcast.feed_parser.fetch_feed", side_effect=Exception("Network error")):
                from plugins.podcast import _podcast_items

                ctx = self._make_ctx()
                result = await _podcast_items(ctx, [
                    "podcast", "items", 0, 100,
                    "url:https://example.com/bad.xml", "menu:1",
                ])
                assert result["count"] == 0
        finally:
            podcast_mod._store = old_store
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)
            podcast_mod._http_client = old_client
            podcast_mod._ctx = old_ctx


# =============================================================================
# podcast search — search PodcastIndex
# =============================================================================


class TestPodcastSearch:
    def _make_ctx(self) -> _FakeCommandContext:
        return _FakeCommandContext()

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_search

            ctx = self._make_ctx()
            result = await _podcast_search(ctx, ["podcast", "search", 0, 100])
            assert result["count"] == 0
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_search_with_term(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            from plugins.podcast.providers import PodcastSearchResult

            mock_results = [
                PodcastSearchResult(name="Found Podcast", url="https://example.com/feed.xml", image="img.jpg", author="Author", provider="podcastindex"),
            ]

            with patch("plugins.podcast.providers.PodcastIndexProvider.search", new_callable=AsyncMock, return_value=mock_results):
                from plugins.podcast import _podcast_search

                ctx = self._make_ctx()
                result = await _podcast_search(ctx, [
                    "podcast", "search", 0, 100, "term:test query", "menu:1",
                ])
                assert result["count"] == 1
                assert result["item_loop"][0]["text"] == "Found Podcast"
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_search_cli_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            from plugins.podcast.providers import PodcastSearchResult

            mock_results = [
                PodcastSearchResult(name="CLI Podcast", url="https://example.com/feed.xml", provider="podcastindex"),
            ]

            with patch("plugins.podcast.providers.PodcastIndexProvider.search", new_callable=AsyncMock, return_value=mock_results):
                from plugins.podcast import _podcast_search

                ctx = self._make_ctx()
                result = await _podcast_search(ctx, [
                    "podcast", "search", 0, 100, "term:test",
                ])
                assert "loop" in result
                assert result["loop"][0]["name"] == "CLI Podcast"
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_search_with_query_param(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            with patch("plugins.podcast.providers.PodcastIndexProvider.search", new_callable=AsyncMock, return_value=[]) as mock_search:
                from plugins.podcast import _podcast_search

                ctx = self._make_ctx()
                result = await _podcast_search(ctx, [
                    "podcast", "search", 0, 100, "query:my podcast",
                ])
                mock_search.assert_called_once_with("my podcast", client=podcast_mod._http_client)
        finally:
            podcast_mod._store = old_store


# =============================================================================
# podcast play — play episode
# =============================================================================


class TestPodcastPlay:
    def _make_ctx(
        self,
        player_id: str = "aa:bb:cc:dd:ee:ff",
        with_player: bool = True,
        with_playlist_manager: bool = True,
    ) -> _FakeCommandContext:
        player = MagicMock() if with_player else None
        playlist = MagicMock()
        playlist.current_index = 0
        playlist_manager = MagicMock() if with_playlist_manager else None
        if playlist_manager is not None:
            playlist_manager.get.return_value = playlist

        ctx = _FakeCommandContext(
            player_id=player_id,
            player=player,
            playlist_manager=playlist_manager,
        )
        return ctx

    @pytest.mark.asyncio
    async def test_play_missing_url(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = MagicMock()

            from plugins.podcast import _podcast_play

            ctx = self._make_ctx()
            result = await _podcast_play(ctx, ["podcast", "play"])
            assert "error" in result
            assert "Missing 'url'" in result["error"]
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_play_episode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_event_bus = podcast_mod._event_bus
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store
            podcast_mod._event_bus = AsyncMock()

            from plugins.podcast import _podcast_play

            ctx = self._make_ctx()

            with patch("resonance.web.handlers.playlist_playback._start_track_stream", new_callable=AsyncMock):
                result = await _podcast_play(ctx, [
                    "podcast", "play", 0, 0,
                    "url:https://example.com/ep.mp3",
                    "title:Test Episode",
                    "feed_title:My Podcast",
                    "cmd:play",
                ])

            assert result == {"count": 1}

            # Should record as recently played
            mock_store.record_played.assert_called_once()
            call_kwargs = mock_store.record_played.call_args
            assert call_kwargs[1]["url"] == "https://example.com/ep.mp3" or call_kwargs.kwargs.get("url") == "https://example.com/ep.mp3"
        finally:
            podcast_mod._store = old_store
            podcast_mod._event_bus = old_event_bus

    @pytest.mark.asyncio
    async def test_play_no_player(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = MagicMock()

            from plugins.podcast import _podcast_play

            ctx = self._make_ctx(with_player=False)
            result = await _podcast_play(ctx, [
                "podcast", "play", 0, 0,
                "url:https://example.com/ep.mp3",
            ])
            assert "error" in result
            assert "No player" in result["error"]
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_play_no_playlist_manager(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = MagicMock()

            from plugins.podcast import _podcast_play

            ctx = self._make_ctx(with_playlist_manager=False)
            result = await _podcast_play(ctx, [
                "podcast", "play", 0, 0,
                "url:https://example.com/ep.mp3",
            ])
            assert "error" in result
            assert "Playlist manager" in result["error"]
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_play_add_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_play

            ctx = self._make_ctx()
            playlist = ctx.playlist_manager.get.return_value

            result = await _podcast_play(ctx, [
                "podcast", "play", 0, 0,
                "url:https://example.com/ep.mp3",
                "cmd:add",
            ])
            assert result == {"count": 1}
            playlist.add.assert_called_once()
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_play_insert_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_play

            ctx = self._make_ctx()
            playlist = ctx.playlist_manager.get.return_value

            result = await _podcast_play(ctx, [
                "podcast", "play", 0, 0,
                "url:https://example.com/ep.mp3",
                "cmd:insert",
            ])
            assert result == {"count": 1}
            playlist.insert.assert_called_once()
        finally:
            podcast_mod._store = old_store


# =============================================================================
# podcast addshow — subscribe
# =============================================================================


class TestPodcastAddshow:
    def _make_ctx(self) -> _FakeCommandContext:
        return _FakeCommandContext()

    @pytest.mark.asyncio
    async def test_addshow_with_name(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.add_subscription.return_value = True
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_addshow

            ctx = self._make_ctx()
            result = await _podcast_addshow(ctx, [
                "podcast", "addshow", 0, 0,
                "url:https://example.com/feed.xml",
                "name:My Podcast",
                "image:cover.jpg",
            ])
            assert result["subscribed"] is True
            assert result["name"] == "My Podcast"
            mock_store.add_subscription.assert_called_once()
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_addshow_missing_url(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = MagicMock()

            from plugins.podcast import _podcast_addshow

            ctx = self._make_ctx()
            result = await _podcast_addshow(ctx, ["podcast", "addshow"])
            assert "error" in result
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_addshow_menu_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.add_subscription.return_value = True
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_addshow

            ctx = self._make_ctx()
            result = await _podcast_addshow(ctx, [
                "podcast", "addshow", 0, 0,
                "url:https://example.com/feed.xml",
                "name:My Podcast",
                "menu:1",
            ])
            assert "item_loop" in result
            assert result["item_loop"][0]["showBriefly"] == 1
            assert "Subscribed" in result["item_loop"][0]["text"]
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_addshow_already_subscribed(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.add_subscription.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_addshow

            ctx = self._make_ctx()
            result = await _podcast_addshow(ctx, [
                "podcast", "addshow", 0, 0,
                "url:https://example.com/feed.xml",
                "name:My Podcast",
                "menu:1",
            ])
            assert "Already subscribed" in result["item_loop"][0]["text"]
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_addshow_without_name_fetches_feed(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_cache = podcast_mod._feed_cache.copy()
        try:
            mock_store = MagicMock()
            mock_store.add_subscription.return_value = True
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store

            # Pre-populate cache
            feed = PodcastFeed(
                title="Auto-Detected Title",
                url="https://example.com/feed.xml",
                image_url="auto-cover.jpg",
            )
            podcast_mod._feed_cache["https://example.com/feed.xml"] = (feed, time.time() + 600)

            from plugins.podcast import _podcast_addshow

            ctx = self._make_ctx()
            result = await _podcast_addshow(ctx, [
                "podcast", "addshow", 0, 0,
                "url:https://example.com/feed.xml",
            ])
            assert result["name"] == "Auto-Detected Title"
        finally:
            podcast_mod._store = old_store
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)


# =============================================================================
# podcast delshow — unsubscribe
# =============================================================================


class TestPodcastDelshow:
    def _make_ctx(self) -> _FakeCommandContext:
        return _FakeCommandContext()

    @pytest.mark.asyncio
    async def test_delshow(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.remove_subscription.return_value = True
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_delshow

            ctx = self._make_ctx()
            result = await _podcast_delshow(ctx, [
                "podcast", "delshow", 0, 0,
                "url:https://example.com/feed.xml",
            ])
            assert result["unsubscribed"] is True
            mock_store.remove_subscription.assert_called_once_with("https://example.com/feed.xml")
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_delshow_missing_url(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = MagicMock()

            from plugins.podcast import _podcast_delshow

            ctx = self._make_ctx()
            result = await _podcast_delshow(ctx, ["podcast", "delshow"])
            assert "error" in result
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_delshow_menu_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.remove_subscription.return_value = True
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_delshow

            ctx = self._make_ctx()
            result = await _podcast_delshow(ctx, [
                "podcast", "delshow", 0, 0,
                "url:https://example.com/feed.xml",
                "name:My Podcast",
                "menu:1",
            ])
            assert "item_loop" in result
            assert "Unsubscribed" in result["item_loop"][0]["text"]
            assert result["item_loop"][0]["nextWindow"] == "grandparent"
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_delshow_not_subscribed(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            mock_store.remove_subscription.return_value = False
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_delshow

            ctx = self._make_ctx()
            result = await _podcast_delshow(ctx, [
                "podcast", "delshow", 0, 0,
                "url:https://example.com/feed.xml",
                "name:My Podcast",
                "menu:1",
            ])
            assert "Not subscribed" in result["item_loop"][0]["text"]
        finally:
            podcast_mod._store = old_store


# =============================================================================
# PodcastIndex search
# =============================================================================


class TestPodcastIndexSearch:
    @pytest.mark.asyncio
    async def test_search_no_client(self):
        from plugins.podcast.providers import PodcastIndexProvider

        provider = PodcastIndexProvider()
        # Search with no client — should create its own internally
        # We mock the internal _pi_get to avoid real network
        with patch("plugins.podcast.providers._pi_get", new_callable=AsyncMock, return_value={}):
            results = await provider.search("test", client=None)
            assert results == []

    @pytest.mark.asyncio
    async def test_search_success(self):
        from plugins.podcast.providers import PodcastIndexProvider

        mock_response = {
            "feeds": [
                {
                    "title": "Found Podcast",
                    "url": "https://example.com/feed.xml",
                    "artwork": "https://example.com/art.jpg",
                    "description": "A great podcast",
                    "author": "Author",
                    "language": "en",
                },
                {
                    "title": "No URL",
                    # Missing url — should be filtered
                },
            ],
        }

        provider = PodcastIndexProvider()
        with patch("plugins.podcast.providers._pi_get", new_callable=AsyncMock, return_value=mock_response):
            results = await provider.search("test query")
            assert len(results) == 1
            assert results[0].name == "Found Podcast"
            assert results[0].url == "https://example.com/feed.xml"
            assert results[0].image == "https://example.com/art.jpg"

    @pytest.mark.asyncio
    async def test_search_network_error(self):
        from plugins.podcast.providers import PodcastIndexProvider

        provider = PodcastIndexProvider()
        with patch("plugins.podcast.providers._pi_get", new_callable=AsyncMock, return_value={}):
            results = await provider.search("test")
            assert results == []

    @pytest.mark.asyncio
    async def test_search_image_fallback(self):
        from plugins.podcast.providers import PodcastIndexProvider

        mock_response = {
            "feeds": [{
                "title": "Test",
                "url": "https://example.com/feed.xml",
                "image": "https://example.com/image.jpg",
                # No "artwork" field — should fall back to "image"
            }],
        }

        provider = PodcastIndexProvider()
        with patch("plugins.podcast.providers._pi_get", new_callable=AsyncMock, return_value=mock_response):
            results = await provider.search("test")
            assert results[0].image == "https://example.com/image.jpg"


# =============================================================================
# PodcastIndex auth headers
# =============================================================================


class TestPodcastIndexHeaders:
    def test_headers_structure(self):
        from plugins.podcast.providers import _podcastindex_headers

        headers = _podcastindex_headers()
        assert "User-Agent" in headers
        assert "X-Auth-Key" in headers
        assert "X-Auth-Date" in headers
        assert "Authorization" in headers
        # Auth date should be a numeric timestamp
        assert headers["X-Auth-Date"].isdigit()
        # Authorization should be a hex SHA-1 hash (40 chars)
        assert len(headers["Authorization"]) == 40


# =============================================================================
# Feed cache
# =============================================================================


class TestFeedCache:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        import plugins.podcast as podcast_mod

        old_cache = podcast_mod._feed_cache.copy()
        old_store = podcast_mod._store
        try:
            podcast_mod._store = None
            feed = PodcastFeed(title="Cached", url="https://example.com/feed.xml")
            podcast_mod._feed_cache["https://example.com/feed.xml"] = (feed, time.time() + 600)

            from plugins.podcast import _get_feed

            result = await _get_feed("https://example.com/feed.xml")
            assert result.title == "Cached"
        finally:
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_cache_expired(self):
        import plugins.podcast as podcast_mod

        old_cache = podcast_mod._feed_cache.copy()
        old_store = podcast_mod._store
        old_client = podcast_mod._http_client
        old_ctx = podcast_mod._ctx
        try:
            podcast_mod._store = None
            podcast_mod._ctx = None
            # Expired cache entry
            feed = PodcastFeed(title="Old", url="https://example.com/feed.xml")
            podcast_mod._feed_cache["https://example.com/feed.xml"] = (feed, time.time() - 1)

            new_feed = PodcastFeed(title="Fresh", url="https://example.com/feed.xml")

            with patch("plugins.podcast.feed_parser.fetch_feed", new_callable=AsyncMock, return_value=new_feed):
                from plugins.podcast import _get_feed

                result = await _get_feed("https://example.com/feed.xml")
                assert result.title == "Fresh"
        finally:
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)
            podcast_mod._store = old_store
            podcast_mod._http_client = old_client
            podcast_mod._ctx = old_ctx

    def test_clear_feed_cache(self):
        import plugins.podcast as podcast_mod

        old_cache = podcast_mod._feed_cache.copy()
        try:
            podcast_mod._feed_cache["test"] = ("data", time.time() + 600)
            assert len(podcast_mod._feed_cache) > 0

            from plugins.podcast import _clear_feed_cache
            _clear_feed_cache()
            assert len(podcast_mod._feed_cache) == 0
        finally:
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)


# =============================================================================
# Plugin lifecycle
# =============================================================================


class TestPluginLifecycle:
    @pytest.mark.asyncio
    async def test_setup_registers_components(self):
        import plugins.podcast as podcast_mod

        # Save original state
        old_store = podcast_mod._store
        old_client = podcast_mod._http_client
        old_event_bus = podcast_mod._event_bus
        old_provider = podcast_mod._provider
        old_ctx = podcast_mod._ctx
        old_refresh = podcast_mod._refresh_task

        try:
            ctx = MagicMock()
            ctx.plugin_id = "podcast"
            ctx.event_bus = MagicMock()
            ctx.ensure_data_dir.return_value = Path("/tmp/test_podcast")
            ctx.get_setting.return_value = 50
            ctx.subscribe = AsyncMock()

            with patch.object(PodcastStore, "load"):
                with patch("httpx.AsyncClient"):
                    await podcast_mod.setup(ctx)

            # Should register command
            ctx.register_command.assert_called_once_with("podcast", podcast_mod.cmd_podcast)

            # Should register content provider
            ctx.register_content_provider.assert_called_once()
            args = ctx.register_content_provider.call_args
            assert args[0][0] == "podcast"

            # Should register menu node
            ctx.register_menu_node.assert_called_once()
            node_args = ctx.register_menu_node.call_args
            assert node_args[1]["node_id"] == "podcasts" or node_args.kwargs.get("node_id") == "podcasts"

            # Should subscribe to player events
            ctx.subscribe.assert_called_once()

            # Module state should be set
            assert podcast_mod._store is not None
            assert podcast_mod._provider is not None
            assert podcast_mod._event_bus is not None
            assert podcast_mod._ctx is not None
        finally:
            # Cancel any background task created during test
            if podcast_mod._refresh_task is not None and podcast_mod._refresh_task is not old_refresh:
                podcast_mod._refresh_task.cancel()
                try:
                    await podcast_mod._refresh_task
                except BaseException:
                    pass
            podcast_mod._store = old_store
            podcast_mod._http_client = old_client
            podcast_mod._event_bus = old_event_bus
            podcast_mod._provider = old_provider
            podcast_mod._ctx = old_ctx
            podcast_mod._refresh_task = old_refresh

    @pytest.mark.asyncio
    async def test_teardown_clears_state(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_client = podcast_mod._http_client
        old_event_bus = podcast_mod._event_bus
        old_provider = podcast_mod._provider
        old_ctx = podcast_mod._ctx
        old_refresh = podcast_mod._refresh_task

        try:
            mock_store = MagicMock()
            mock_client = AsyncMock()
            podcast_mod._store = mock_store
            podcast_mod._http_client = mock_client
            podcast_mod._event_bus = MagicMock()
            podcast_mod._provider = MagicMock()
            podcast_mod._ctx = MagicMock()
            podcast_mod._refresh_task = None
            podcast_mod._feed_cache["test"] = ("data", time.time() + 600)
            podcast_mod._player_tracking.clear()

            ctx = MagicMock()
            await podcast_mod.teardown(ctx)

            assert podcast_mod._store is None
            assert podcast_mod._http_client is None
            assert podcast_mod._event_bus is None
            assert podcast_mod._provider is None
            assert podcast_mod._ctx is None
            assert len(podcast_mod._feed_cache) == 0

            mock_store.save.assert_called_once()
            mock_client.aclose.assert_called_once()
        finally:
            podcast_mod._store = old_store
            podcast_mod._http_client = old_client
            podcast_mod._event_bus = old_event_bus
            podcast_mod._provider = old_provider
            podcast_mod._ctx = old_ctx
            podcast_mod._refresh_task = old_refresh

    @pytest.mark.asyncio
    async def test_setup_menu_weight(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_client = podcast_mod._http_client
        old_event_bus = podcast_mod._event_bus
        old_provider = podcast_mod._provider
        old_ctx = podcast_mod._ctx
        old_refresh = podcast_mod._refresh_task

        try:
            ctx = MagicMock()
            ctx.plugin_id = "podcast"
            ctx.event_bus = MagicMock()
            ctx.ensure_data_dir.return_value = Path("/tmp/test_podcast")
            ctx.get_setting.return_value = 50
            ctx.subscribe = AsyncMock()

            with patch.object(PodcastStore, "load"):
                with patch("httpx.AsyncClient"):
                    await podcast_mod.setup(ctx)

            node_call = ctx.register_menu_node.call_args
            # Weight should be 50 (between Radio=45 and Favorites=55)
            assert node_call.kwargs.get("weight") == 50 or node_call[1].get("weight") == 50
        finally:
            # Cancel any background task created during test
            if podcast_mod._refresh_task is not None and podcast_mod._refresh_task is not old_refresh:
                podcast_mod._refresh_task.cancel()
                try:
                    await podcast_mod._refresh_task
                except BaseException:
                    pass
            podcast_mod._store = old_store
            podcast_mod._http_client = old_client
            podcast_mod._event_bus = old_event_bus
            podcast_mod._provider = old_provider
            podcast_mod._ctx = old_ctx
            podcast_mod._refresh_task = old_refresh


# =============================================================================
# Integration-style tests
# =============================================================================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_subscribe_browse_flow(self):
        """Test the full subscribe → browse episodes flow."""
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_cache = podcast_mod._feed_cache.copy()
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.add_subscription.return_value = True
            mock_store.is_subscribed.return_value = True
            mock_store.recent_count = 0
            mock_store.subscription_count = 1
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = [
                Subscription(name="Test Podcast", url="https://example.com/feed.xml"),
            ]
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            # Subscribe
            from plugins.podcast import _podcast_addshow, _podcast_items

            ctx = _FakeCommandContext()
            result = await _podcast_addshow(ctx, [
                "podcast", "addshow", 0, 0,
                "url:https://example.com/feed.xml",
                "name:Test Podcast",
            ])
            assert result["subscribed"] is True

            # Browse root — should show subscription
            result = await _podcast_items(ctx, ["podcast", "items", 0, 100, "menu:1"])
            assert result["count"] >= 1

            # Pre-populate feed cache for episode browsing
            feed = PodcastFeed(
                title="Test Podcast",
                url="https://example.com/feed.xml",
                episodes=[
                    PodcastEpisode(
                        title="First Episode",
                        url="https://example.com/ep1.mp3",
                        guid="ep1",
                        published="2024-01-01T00:00:00+00:00",
                        published_epoch=1704067200.0,
                        duration_seconds=1800,
                        content_type="audio/mpeg",
                    ),
                ],
            )
            podcast_mod._feed_cache["https://example.com/feed.xml"] = (feed, time.time() + 600)

            # Browse feed — should show episode
            result = await _podcast_items(ctx, [
                "podcast", "items", 0, 100,
                "url:https://example.com/feed.xml",
                "menu:1",
            ])
            assert result["count"] == 1
            assert result["item_loop"][0]["text"] == "First Episode"
        finally:
            podcast_mod._store = old_store
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)
            podcast_mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_search_then_subscribe_flow(self):
        """Test searching for a podcast and subscribing to it."""
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.add_subscription.return_value = True
            mock_store.is_subscribed.return_value = False
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            from plugins.podcast import _podcast_addshow, _podcast_search
            from plugins.podcast.providers import PodcastSearchResult

            ctx = _FakeCommandContext()

            # Search
            mock_results = [
                PodcastSearchResult(
                    name="Discovered Podcast",
                    url="https://example.com/found.xml",
                    image="cover.jpg",
                    author="Author",
                    provider="podcastindex",
                ),
            ]
            with patch("plugins.podcast.providers.PodcastIndexProvider.search", new_callable=AsyncMock, return_value=mock_results):
                result = await _podcast_search(ctx, [
                    "podcast", "search", 0, 100, "term:test", "menu:1",
                ])
                assert result["count"] == 1
                feed_url = result["item_loop"][0]["actions"]["go"]["params"]["url"]

            # Subscribe to found feed
            result = await _podcast_addshow(ctx, [
                "podcast", "addshow", 0, 0,
                f"url:{feed_url}",
                "name:Discovered Podcast",
            ])
            assert result["subscribed"] is True
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_store_persistence_roundtrip(self, tmp_path: Path):
        """Test that store data survives save/load cycle."""
        store = PodcastStore(tmp_path)

        # Add various data
        store.add_subscription(
            url="https://a.com/feed.xml",
            name="Podcast A",
            image="a.jpg",
            author="Author A",
        )
        store.add_subscription(
            url="https://b.com/feed.xml",
            name="Podcast B",
        )
        store.set_resume_position("https://a.com/ep1.mp3", 120)
        store.set_resume_position("https://a.com/ep2.mp3", 3500)
        store.record_played(
            url="https://a.com/ep1.mp3",
            title="Episode 1",
            show="Podcast A",
            duration=3600,
        )
        store.record_played(
            url="https://b.com/ep1.mp3",
            title="B Episode 1",
            show="Podcast B",
        )

        # Load into new store
        store2 = PodcastStore(tmp_path)
        store2.load()

        assert store2.subscription_count == 2
        assert store2.is_subscribed("https://a.com/feed.xml")
        assert store2.is_subscribed("https://b.com/feed.xml")
        assert store2.get_subscription("https://a.com/feed.xml").name == "Podcast A"
        assert store2.get_subscription("https://a.com/feed.xml").image == "a.jpg"

        assert store2.get_resume_position("https://a.com/ep1.mp3") == 120
        assert store2.get_resume_position("https://a.com/ep2.mp3") == 3500

        assert store2.recent_count == 2
        assert store2.recent[0].title == "B Episode 1"  # newest first
        assert store2.recent[1].title == "Episode 1"


# =============================================================================
# v2 feature tests — Episode progress tracking
# =============================================================================


class TestEpisodeProgress:
    @pytest.fixture
    def store(self, tmp_path: Path) -> PodcastStore:
        return PodcastStore(tmp_path)

    def test_no_progress_by_default(self, store: PodcastStore):
        assert store.get_progress("https://example.com/ep.mp3") is None
        assert store.get_progress_percentage("https://example.com/ep.mp3") == 0.0
        assert store.progress_count == 0

    def test_update_progress(self, store: PodcastStore):
        store.update_progress("https://example.com/ep.mp3", 300, 3600)
        prog = store.get_progress("https://example.com/ep.mp3")
        assert prog is not None
        assert prog.position == 300
        assert prog.duration == 3600
        assert prog.percentage == pytest.approx(8.3, abs=0.1)
        assert prog.updated_at > 0

    def test_progress_percentage(self, store: PodcastStore):
        store.update_progress("https://example.com/ep.mp3", 1800, 3600)
        assert store.get_progress_percentage("https://example.com/ep.mp3") == pytest.approx(50.0, abs=0.1)

    def test_progress_100_percent(self, store: PodcastStore):
        store.update_progress("https://example.com/ep.mp3", 3600, 3600)
        assert store.get_progress_percentage("https://example.com/ep.mp3") == 100.0

    def test_progress_zero_duration_ignored(self, store: PodcastStore):
        store.update_progress("https://example.com/ep.mp3", 300, 0)
        assert store.get_progress("https://example.com/ep.mp3") is None

    def test_clear_progress(self, store: PodcastStore):
        store.update_progress("https://example.com/ep.mp3", 300, 3600)
        store.clear_progress("https://example.com/ep.mp3")
        assert store.get_progress("https://example.com/ep.mp3") is None

    def test_get_all_progress(self, store: PodcastStore):
        store.update_progress("https://example.com/ep1.mp3", 100, 1000)
        store.update_progress("https://example.com/ep2.mp3", 500, 2000)
        all_prog = store.get_all_progress()
        assert len(all_prog) == 2
        assert "https://example.com/ep1.mp3" in all_prog
        assert "https://example.com/ep2.mp3" in all_prog

    def test_auto_mark_played_at_threshold(self, store: PodcastStore):
        """Progress past the auto-mark threshold should mark as played."""
        # Default threshold is 90%
        store.update_progress("https://example.com/ep.mp3", 910, 1000)
        assert store.is_played("https://example.com/ep.mp3")

    def test_no_auto_mark_below_threshold(self, store: PodcastStore):
        store.update_progress("https://example.com/ep.mp3", 500, 1000)
        assert not store.is_played("https://example.com/ep.mp3")

    def test_custom_auto_mark_threshold(self, tmp_path: Path):
        store = PodcastStore(tmp_path, auto_mark_played_percent=100)
        store.update_progress("https://example.com/ep.mp3", 950, 1000)
        # At 95%, should NOT be marked played with threshold=100
        assert not store.is_played("https://example.com/ep.mp3")
        store.update_progress("https://example.com/ep.mp3", 1000, 1000)
        assert store.is_played("https://example.com/ep.mp3")

    def test_progress_persists_across_save_load(self, tmp_path: Path):
        store = PodcastStore(tmp_path)
        store.update_progress("https://example.com/ep.mp3", 600, 3600)
        store.save()

        store2 = PodcastStore(tmp_path)
        store2.load()
        prog = store2.get_progress("https://example.com/ep.mp3")
        assert prog is not None
        assert prog.position == 600
        assert prog.duration == 3600
        assert prog.percentage == pytest.approx(16.7, abs=0.1)

    def test_episode_progress_from_dict(self):
        data = {"position": 300, "duration": 3600, "percentage": 8.3, "updated_at": 1234567890.0}
        ep = EpisodeProgress.from_dict(data)
        assert ep.position == 300
        assert ep.duration == 3600
        assert ep.percentage == 8.3
        assert ep.updated_at == 1234567890.0

    def test_episode_progress_to_dict(self):
        ep = EpisodeProgress(position=300, duration=3600, percentage=8.3, updated_at=1234567890.0)
        d = ep.to_dict()
        assert d["position"] == 300
        assert d["duration"] == 3600
        assert d["percentage"] == 8.3
        assert d["updated_at"] == 1234567890.0


# =============================================================================
# v2 feature tests — Played / unplayed state
# =============================================================================


class TestPlayedUnplayed:
    @pytest.fixture
    def store(self, tmp_path: Path) -> PodcastStore:
        return PodcastStore(tmp_path)

    def test_not_played_by_default(self, store: PodcastStore):
        assert not store.is_played("https://example.com/ep.mp3")
        assert store.played_count == 0

    def test_mark_played(self, store: PodcastStore):
        store.mark_played("https://example.com/ep.mp3")
        assert store.is_played("https://example.com/ep.mp3")
        assert store.played_count == 1

    def test_mark_played_idempotent(self, store: PodcastStore):
        store.mark_played("https://example.com/ep.mp3")
        store.mark_played("https://example.com/ep.mp3")
        assert store.played_count == 1

    def test_mark_unplayed(self, store: PodcastStore):
        store.mark_played("https://example.com/ep.mp3")
        store.set_resume_position("https://example.com/ep.mp3", 300)
        store.update_progress("https://example.com/ep.mp3", 300, 3600)

        store.mark_unplayed("https://example.com/ep.mp3")
        assert not store.is_played("https://example.com/ep.mp3")
        assert store.get_resume_position("https://example.com/ep.mp3") == 0
        assert store.get_progress("https://example.com/ep.mp3") is None

    def test_mark_all_played(self, store: PodcastStore):
        urls = [f"https://example.com/ep{i}.mp3" for i in range(5)]
        changed = store.mark_all_played(urls)
        assert changed == 5
        for url in urls:
            assert store.is_played(url)

    def test_mark_all_played_partial(self, store: PodcastStore):
        store.mark_played("https://example.com/ep0.mp3")
        urls = [f"https://example.com/ep{i}.mp3" for i in range(3)]
        changed = store.mark_all_played(urls)
        assert changed == 2  # ep0 was already played

    def test_played_episodes_property(self, store: PodcastStore):
        store.mark_played("https://example.com/ep1.mp3")
        store.mark_played("https://example.com/ep2.mp3")
        played = store.played_episodes
        assert len(played) == 2
        assert "https://example.com/ep1.mp3" in played
        assert isinstance(played, set)

    def test_played_persists_across_save_load(self, tmp_path: Path):
        store = PodcastStore(tmp_path)
        store.mark_played("https://example.com/ep1.mp3")
        store.mark_played("https://example.com/ep2.mp3")
        store.save()

        store2 = PodcastStore(tmp_path)
        store2.load()
        assert store2.is_played("https://example.com/ep1.mp3")
        assert store2.is_played("https://example.com/ep2.mp3")
        assert store2.played_count == 2

    def test_resume_near_end_auto_marks_played(self, store: PodcastStore):
        """Setting resume position near the end should auto-mark as played."""
        store.set_resume_position("https://example.com/ep.mp3", 3590, duration=3600)
        assert store.is_played("https://example.com/ep.mp3")


# =============================================================================
# v2 feature tests — Continue listening
# =============================================================================


class TestContinueListening:
    @pytest.fixture
    def store(self, tmp_path: Path) -> PodcastStore:
        return PodcastStore(tmp_path)

    def test_empty_when_no_progress(self, store: PodcastStore):
        assert store.get_in_progress_episodes() == []

    def test_in_progress_episodes(self, store: PodcastStore):
        store.update_progress("https://example.com/ep1.mp3", 300, 3600)
        store.update_progress("https://example.com/ep2.mp3", 600, 1800)

        in_prog = store.get_in_progress_episodes()
        assert len(in_prog) == 2
        # Should contain url, position, duration, percentage
        urls = {e["url"] for e in in_prog}
        assert "https://example.com/ep1.mp3" in urls
        assert "https://example.com/ep2.mp3" in urls

    def test_played_episodes_excluded(self, store: PodcastStore):
        store.update_progress("https://example.com/ep1.mp3", 300, 3600)
        store.mark_played("https://example.com/ep1.mp3")

        in_prog = store.get_in_progress_episodes()
        assert len(in_prog) == 0

    def test_minimal_progress_excluded(self, store: PodcastStore):
        """Episodes with less than threshold seconds are excluded."""
        store.update_progress("https://example.com/ep.mp3", 5, 3600)
        in_prog = store.get_in_progress_episodes()
        assert len(in_prog) == 0

    def test_sorted_by_most_recent(self, store: PodcastStore):
        store.update_progress("https://example.com/ep1.mp3", 300, 3600)
        store.update_progress("https://example.com/ep2.mp3", 600, 1800)

        in_prog = store.get_in_progress_episodes()
        # ep2 was updated last, should be first
        assert in_prog[0]["url"] == "https://example.com/ep2.mp3"

    def test_enriched_with_recent_metadata(self, store: PodcastStore):
        store.record_played(
            url="https://example.com/ep.mp3",
            title="Test Episode",
            show="My Show",
            image="cover.jpg",
            feed_url="https://example.com/feed.xml",
        )
        store.update_progress("https://example.com/ep.mp3", 300, 3600)

        in_prog = store.get_in_progress_episodes()
        assert len(in_prog) == 1
        assert in_prog[0]["title"] == "Test Episode"
        assert in_prog[0]["show"] == "My Show"
        assert in_prog[0]["image"] == "cover.jpg"


# =============================================================================
# v2 feature tests — Subscription management extras
# =============================================================================


class TestSubscriptionExtras:
    @pytest.fixture
    def store(self, tmp_path: Path) -> PodcastStore:
        return PodcastStore(tmp_path)

    def test_move_subscription_down(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.add_subscription(url="https://b.com/feed.xml", name="B")
        store.add_subscription(url="https://c.com/feed.xml", name="C")

        result = store.move_subscription("https://a.com/feed.xml", 1)
        assert result is True
        names = [s.name for s in store.subscriptions]
        assert names == ["B", "A", "C"]

    def test_move_subscription_up(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.add_subscription(url="https://b.com/feed.xml", name="B")
        store.add_subscription(url="https://c.com/feed.xml", name="C")

        result = store.move_subscription("https://c.com/feed.xml", -1)
        assert result is True
        names = [s.name for s in store.subscriptions]
        assert names == ["A", "C", "B"]

    def test_move_subscription_boundary(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.add_subscription(url="https://b.com/feed.xml", name="B")

        # Can't move first item up
        assert store.move_subscription("https://a.com/feed.xml", -1) is False
        # Can't move last item down
        assert store.move_subscription("https://b.com/feed.xml", 1) is False

    def test_move_nonexistent_subscription(self, store: PodcastStore):
        assert store.move_subscription("https://nope.com/feed.xml", 1) is False

    def test_import_subscriptions(self, store: PodcastStore):
        feeds = [
            {"url": "https://a.com/feed.xml", "name": "Podcast A", "image": "a.jpg"},
            {"url": "https://b.com/feed.xml", "name": "Podcast B"},
            {"url": "https://c.com/feed.xml", "name": "Podcast C"},
        ]
        added, skipped = store.import_subscriptions(feeds)
        assert added == 3
        assert skipped == 0
        assert store.subscription_count == 3

    def test_import_subscriptions_dedup(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="Existing")
        feeds = [
            {"url": "https://a.com/feed.xml", "name": "Podcast A"},
            {"url": "https://b.com/feed.xml", "name": "Podcast B"},
        ]
        added, skipped = store.import_subscriptions(feeds)
        assert added == 1
        assert skipped == 1

    def test_import_subscriptions_empty_url_skipped(self, store: PodcastStore):
        feeds = [{"url": "", "name": "Bad"}, {"name": "No URL"}]
        added, skipped = store.import_subscriptions(feeds)
        assert added == 0
        assert skipped == 2

    def test_mark_feed_browsed(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.set_new_episode_count("https://a.com/feed.xml", 5)
        assert store.get_subscription("https://a.com/feed.xml").new_episode_count == 5

        store.mark_feed_browsed("https://a.com/feed.xml")
        sub = store.get_subscription("https://a.com/feed.xml")
        assert sub.new_episode_count == 0
        assert sub.last_browsed_at > 0

    def test_set_new_episode_count(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.set_new_episode_count("https://a.com/feed.xml", 3)
        assert store.get_subscription("https://a.com/feed.xml").new_episode_count == 3

    def test_total_new_episodes(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.add_subscription(url="https://b.com/feed.xml", name="B")
        store.set_new_episode_count("https://a.com/feed.xml", 3)
        store.set_new_episode_count("https://b.com/feed.xml", 7)
        assert store.total_new_episodes == 10

    def test_export_subscriptions(self, store: PodcastStore):
        store.add_subscription(url="https://a.com/feed.xml", name="A", image="a.jpg")
        store.add_subscription(url="https://b.com/feed.xml", name="B")
        exported = store.export_subscriptions()
        assert len(exported) == 2
        assert exported[0]["url"] == "https://a.com/feed.xml"
        assert exported[0]["name"] == "A"


# =============================================================================
# v2 feature tests — Store stats
# =============================================================================


class TestStoreStats:
    def test_get_stats(self, tmp_path: Path):
        store = PodcastStore(tmp_path)
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.set_resume_position("https://a.com/ep1.mp3", 300)
        store.record_played(url="https://a.com/ep1.mp3", title="Ep 1")
        store.mark_played("https://a.com/ep2.mp3")
        store.update_progress("https://a.com/ep3.mp3", 100, 1000)

        stats = store.get_stats()
        assert stats["subscriptions"] == 1
        assert stats["resume_positions"] == 1
        assert stats["recent_episodes"] == 1
        assert stats["played_episodes"] == 1
        assert stats["total_progress_entries"] == 1

    def test_clear_all_v2(self, tmp_path: Path):
        store = PodcastStore(tmp_path)
        store.add_subscription(url="https://a.com/feed.xml", name="A")
        store.set_resume_position("https://a.com/ep.mp3", 300)
        store.record_played(url="https://a.com/ep.mp3", title="Ep")
        store.mark_played("https://a.com/ep.mp3")
        store.update_progress("https://a.com/ep.mp3", 300, 3600)

        store.clear_all()
        assert store.subscription_count == 0
        assert store.recent_count == 0
        assert store.played_count == 0
        assert store.progress_count == 0
        assert len(store.resume_positions) == 0


# =============================================================================
# v2 feature tests — OPML import / export
# =============================================================================


class TestOPML:
    def test_parse_opml_basic(self):
        from plugins.podcast.opml import parse_opml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>My Podcasts</title></head>
  <body>
    <outline type="rss" text="Podcast A" xmlUrl="https://a.com/feed.xml"/>
    <outline type="rss" text="Podcast B" xmlUrl="https://b.com/feed.xml" htmlUrl="https://b.com"/>
  </body>
</opml>"""

        doc = parse_opml(xml)
        assert doc.title == "My Podcasts"
        assert doc.feed_count == 2
        assert doc.feeds[0].url == "https://a.com/feed.xml"
        assert doc.feeds[0].name == "Podcast A"
        assert doc.feeds[1].url == "https://b.com/feed.xml"
        assert doc.feeds[1].html_url == "https://b.com"

    def test_parse_opml_nested_folders(self):
        from plugins.podcast.opml import parse_opml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Organized</title></head>
  <body>
    <outline text="Tech">
      <outline type="rss" text="Tech Pod" xmlUrl="https://tech.com/feed.xml"/>
    </outline>
    <outline text="Comedy">
      <outline type="rss" text="Fun Pod" xmlUrl="https://fun.com/feed.xml"/>
    </outline>
  </body>
</opml>"""

        doc = parse_opml(xml)
        assert doc.feed_count == 2
        assert doc.feeds[0].categories == ["Tech"]
        assert doc.feeds[1].categories == ["Comedy"]

    def test_parse_opml_deduplication(self):
        from plugins.podcast.opml import parse_opml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Dupes</title></head>
  <body>
    <outline type="rss" text="Pod A" xmlUrl="https://a.com/feed.xml"/>
    <outline type="rss" text="Pod A Copy" xmlUrl="https://a.com/feed.xml"/>
  </body>
</opml>"""

        doc = parse_opml(xml)
        assert doc.feed_count == 1

    def test_parse_opml_no_body_raises(self):
        from plugins.podcast.opml import parse_opml

        xml = '<?xml version="1.0"?><opml version="2.0"><head/></opml>'
        with pytest.raises(ValueError, match="No <body>"):
            parse_opml(xml)

    def test_parse_opml_safe_returns_none_on_error(self):
        from plugins.podcast.opml import parse_opml_safe

        assert parse_opml_safe("not xml at all") is None

    def test_generate_opml(self):
        from plugins.podcast.opml import generate_opml

        feeds = [
            {"url": "https://a.com/feed.xml", "name": "Podcast A", "image": "a.jpg"},
            {"url": "https://b.com/feed.xml", "name": "Podcast B"},
        ]
        xml = generate_opml(feeds, title="Test Export")
        assert '<?xml version="1.0" encoding="UTF-8"?>' in xml
        assert 'xmlUrl="https://a.com/feed.xml"' in xml
        assert 'xmlUrl="https://b.com/feed.xml"' in xml
        assert "Test Export" in xml
        assert 'type="rss"' in xml

    def test_generate_opml_skips_empty_url(self):
        from plugins.podcast.opml import generate_opml

        feeds = [
            {"url": "", "name": "Bad"},
            {"url": "https://a.com/feed.xml", "name": "Good"},
        ]
        xml = generate_opml(feeds)
        assert "Bad" not in xml
        assert "Good" in xml

    def test_roundtrip_opml(self):
        from plugins.podcast.opml import generate_opml, parse_opml

        feeds = [
            {"url": "https://a.com/feed.xml", "name": "Podcast A"},
            {"url": "https://b.com/feed.xml", "name": "Podcast B", "description": "Great show"},
        ]
        xml = generate_opml(feeds)
        doc = parse_opml(xml)
        assert doc.feed_count == 2
        assert doc.feeds[0].url == "https://a.com/feed.xml"
        assert doc.feeds[0].name == "Podcast A"
        assert doc.feeds[1].url == "https://b.com/feed.xml"

    def test_opml_file_roundtrip(self, tmp_path: Path):
        from plugins.podcast.opml import export_opml_file, import_opml_file

        feeds = [
            {"url": "https://a.com/feed.xml", "name": "Podcast A"},
            {"url": "https://b.com/feed.xml", "name": "Podcast B"},
        ]
        out_path = tmp_path / "subs.opml"
        export_opml_file(out_path, feeds)
        assert out_path.is_file()

        doc = import_opml_file(out_path)
        assert doc.feed_count == 2
        assert doc.feeds[0].url == "https://a.com/feed.xml"

    def test_opml_document_to_dict(self):
        from plugins.podcast.opml import OPMLDocument, OPMLFeed

        doc = OPMLDocument(
            title="Test",
            feeds=[
                OPMLFeed(url="https://a.com/feed.xml", name="A"),
            ],
        )
        d = doc.to_dict()
        assert d["title"] == "Test"
        assert d["feed_count"] == 1
        assert d["feeds"][0]["url"] == "https://a.com/feed.xml"


# =============================================================================
# v2 feature tests — Search providers
# =============================================================================


class TestProviders:
    def test_provider_registry(self):
        from plugins.podcast.providers import get_all_providers, get_provider, list_provider_names

        names = list_provider_names()
        assert "podcastindex" in names
        assert "gpodder" in names
        assert "itunes" in names

        providers = get_all_providers()
        assert len(providers) >= 3

    def test_get_provider_default(self):
        from plugins.podcast.providers import get_provider

        provider = get_provider("podcastindex")
        assert provider.name == "podcastindex"
        assert provider.display_name == "PodcastIndex"
        assert provider.supports_trending is True
        assert provider.supports_new_episodes is True

    def test_get_provider_gpodder(self):
        from plugins.podcast.providers import get_provider

        provider = get_provider("gpodder")
        assert provider.name == "gpodder"
        assert provider.display_name == "GPodder"
        assert provider.supports_trending is True

    def test_get_provider_itunes(self):
        from plugins.podcast.providers import get_provider

        provider = get_provider("itunes")
        assert provider.name == "itunes"
        assert provider.display_name == "iTunes / Apple Podcasts"
        assert provider.supports_trending is True

    def test_get_provider_unknown_fallback(self):
        from plugins.podcast.providers import get_provider

        provider = get_provider("nonexistent")
        assert provider.name == "podcastindex"  # falls back

    def test_search_result_to_dict(self):
        from plugins.podcast.providers import PodcastSearchResult

        result = PodcastSearchResult(
            name="Test Pod",
            url="https://example.com/feed.xml",
            image="img.jpg",
            author="Author",
            categories=["Tech", "News"],
            episode_count=42,
            provider="podcastindex",
        )
        d = result.to_dict()
        assert d["name"] == "Test Pod"
        assert d["url"] == "https://example.com/feed.xml"
        assert d["image"] == "img.jpg"
        assert d["categories"] == ["Tech", "News"]
        assert d["episode_count"] == 42
        assert d["provider"] == "podcastindex"

    def test_search_result_to_dict_minimal(self):
        from plugins.podcast.providers import PodcastSearchResult

        result = PodcastSearchResult(name="Minimal", url="https://example.com/feed.xml")
        d = result.to_dict()
        assert d == {"name": "Minimal", "url": "https://example.com/feed.xml"}

    def test_new_episode_result_to_dict(self):
        from plugins.podcast.providers import NewEpisodeResult

        ep = NewEpisodeResult(
            title="New Ep",
            url="https://example.com/ep.mp3",
            feed_url="https://example.com/feed.xml",
            feed_title="My Show",
            published_epoch=1700000000.0,
            duration_seconds=3600,
        )
        d = ep.to_dict()
        assert d["title"] == "New Ep"
        assert d["url"] == "https://example.com/ep.mp3"
        assert d["feed_url"] == "https://example.com/feed.xml"
        assert d["duration_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_podcastindex_empty_search(self):
        from plugins.podcast.providers import PodcastIndexProvider

        provider = PodcastIndexProvider()
        results = await provider.search("", client=MagicMock())
        assert results == []

    @pytest.mark.asyncio
    async def test_gpodder_empty_search(self):
        from plugins.podcast.providers import GPodderProvider

        provider = GPodderProvider()
        results = await provider.search("", client=MagicMock())
        assert results == []

    @pytest.mark.asyncio
    async def test_itunes_empty_search(self):
        from plugins.podcast.providers import ITunesSearchProvider

        provider = ITunesSearchProvider()
        results = await provider.search("", client=MagicMock())
        assert results == []


# =============================================================================
# v2 feature tests — Resume submenu
# =============================================================================


class TestResumeSubmenu:
    @pytest.mark.asyncio
    async def test_resume_submenu_menu_mode(self):
        from plugins.podcast import _build_resume_submenu

        tagged = {
            "ep_url": "https://example.com/ep.mp3",
            "ep_title": "Test Episode",
            "ep_icon": "cover.jpg",
            "feed_url": "https://example.com/feed.xml",
            "feed_title": "My Show",
            "duration": "3600",
            "resume_pos": "600",
            "content_type": "audio/mpeg",
        }
        result = _build_resume_submenu(tagged, is_menu=True)
        assert result["count"] == 3  # resume + beginning + mark played
        items = result["item_loop"]
        assert "Resume" in items[0]["text"]
        assert "10:00" in items[0]["text"]
        assert items[1]["text"] == "Play from beginning"
        assert items[2]["text"] == "Mark as played"

    @pytest.mark.asyncio
    async def test_resume_submenu_cli_mode(self):
        from plugins.podcast import _build_resume_submenu

        tagged = {
            "ep_url": "https://example.com/ep.mp3",
            "ep_title": "Test Episode",
            "resume_pos": "600",
            "duration": "3600",
        }
        result = _build_resume_submenu(tagged, is_menu=False)
        assert result["count"] == 2
        assert result["loop"][0]["from"] == 600
        assert result["loop"][1]["from"] == 0


# =============================================================================
# v2 feature tests — What's New
# =============================================================================


class TestWhatsNew:
    @pytest.mark.asyncio
    async def test_whatsnew_aggregates_episodes(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_cache = podcast_mod._feed_cache.copy()
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.subscription_count = 2
            mock_store.total_new_episodes = 0
            mock_store.get_in_progress_episodes.return_value = []
            mock_store.subscriptions = [
                Subscription(name="Pod A", url="https://a.com/feed.xml"),
                Subscription(name="Pod B", url="https://b.com/feed.xml"),
            ]
            mock_store.get_resume_position.return_value = 0
            mock_store.get_progress_percentage.return_value = 0.0
            mock_store.is_played.return_value = False
            podcast_mod._store = mock_store

            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.side_effect = lambda key: {
                "new_since_days": 7,
                "max_new_episodes": 50,
                "search_provider": "podcastindex",
            }.get(key, 7)

            now = time.time()
            feed_a = PodcastFeed(
                title="Pod A",
                url="https://a.com/feed.xml",
                episodes=[
                    PodcastEpisode(
                        title="A Recent",
                        url="https://a.com/ep1.mp3",
                        published_epoch=now - 3600,
                    ),
                ],
            )
            feed_b = PodcastFeed(
                title="Pod B",
                url="https://b.com/feed.xml",
                episodes=[
                    PodcastEpisode(
                        title="B Recent",
                        url="https://b.com/ep1.mp3",
                        published_epoch=now - 7200,
                    ),
                ],
            )
            podcast_mod._feed_cache["https://a.com/feed.xml"] = (feed_a, now + 600)
            podcast_mod._feed_cache["https://b.com/feed.xml"] = (feed_b, now + 600)

            from plugins.podcast import _build_whatsnew

            result = await _build_whatsnew(0, 100, is_menu=False)
            assert result["count"] == 2
            # Sorted newest first
            assert result["loop"][0]["name"] == "A Recent"
            assert result["loop"][1]["name"] == "B Recent"
        finally:
            podcast_mod._store = old_store
            podcast_mod._feed_cache.clear()
            podcast_mod._feed_cache.update(old_cache)
            podcast_mod._ctx = old_ctx


# =============================================================================
# v2 feature tests — Stats command
# =============================================================================


class TestStatsCommand:
    def test_podcast_stats(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_ctx = podcast_mod._ctx
        try:
            mock_store = MagicMock()
            mock_store.get_stats.return_value = {
                "subscriptions": 5,
                "resume_positions": 3,
                "recent_episodes": 10,
                "played_episodes": 20,
                "in_progress_episodes": 2,
                "total_progress_entries": 15,
                "total_new_episodes": 7,
            }
            podcast_mod._store = mock_store
            podcast_mod._ctx = MagicMock()
            podcast_mod._ctx.get_setting.return_value = "podcastindex"

            from plugins.podcast import _podcast_stats

            result = _podcast_stats()
            assert result["subscriptions"] == 5
            assert result["played_episodes"] == 20
            assert result["provider"] == "podcastindex"
            assert "cache_size" in result
            assert "tracking_players" in result
        finally:
            podcast_mod._store = old_store
            podcast_mod._ctx = old_ctx


# =============================================================================
# v2 feature tests — Mark played / unplayed commands
# =============================================================================


class TestMarkPlayedCommands:
    @pytest.mark.asyncio
    async def test_markplayed_single_episode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_markplayed

            ctx = _FakeCommandContext()
            result = await _podcast_markplayed(ctx, [
                "podcast", "markplayed", "url:https://example.com/ep.mp3",
            ])
            mock_store.mark_played.assert_called_once_with("https://example.com/ep.mp3")
            assert result["count"] == 1
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_markplayed_menu_mode(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_markplayed

            ctx = _FakeCommandContext()
            result = await _podcast_markplayed(ctx, [
                "podcast", "markplayed", "url:https://example.com/ep.mp3", "menu:1",
            ])
            assert "item_loop" in result
            assert result["item_loop"][0]["showBriefly"] == 1

        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_markplayed_missing_url(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            podcast_mod._store = MagicMock()

            from plugins.podcast import _podcast_markplayed

            ctx = _FakeCommandContext()
            result = await _podcast_markplayed(ctx, ["podcast", "markplayed"])
            assert "error" in result
        finally:
            podcast_mod._store = old_store

    @pytest.mark.asyncio
    async def test_markunplayed(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _podcast_markunplayed

            ctx = _FakeCommandContext()
            result = await _podcast_markunplayed(ctx, [
                "podcast", "markunplayed", "url:https://example.com/ep.mp3",
            ])
            mock_store.mark_unplayed.assert_called_once_with("https://example.com/ep.mp3")
            assert result["count"] == 1
        finally:
            podcast_mod._store = old_store


# =============================================================================
# v2 feature tests — Event-based resume tracking
# =============================================================================


class TestEventBasedResume:
    @pytest.mark.asyncio
    async def test_player_status_playing_podcast(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_tracking = dict(podcast_mod._player_tracking)
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _on_player_status

            event = MagicMock()
            event.player_id = "aa:bb:cc:dd:ee:ff"
            event.state = "playing"
            event.elapsed_seconds = 120.0
            event.duration = 3600.0
            event.current_track = {
                "source": "podcast",
                "path": "https://example.com/ep.mp3",
                "title": "Test Episode",
                "artist": "My Show",
            }

            await _on_player_status(event)

            # Should be tracking this player
            assert "aa:bb:cc:dd:ee:ff" in podcast_mod._player_tracking
            tracking = podcast_mod._player_tracking["aa:bb:cc:dd:ee:ff"]
            assert tracking["url"] == "https://example.com/ep.mp3"
            assert tracking["elapsed"] == 120.0
        finally:
            podcast_mod._store = old_store
            podcast_mod._player_tracking.clear()
            podcast_mod._player_tracking.update(old_tracking)

    @pytest.mark.asyncio
    async def test_player_status_stopped_saves_position(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_tracking = dict(podcast_mod._player_tracking)
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            # Pre-populate tracking
            podcast_mod._player_tracking["aa:bb:cc:dd:ee:ff"] = {
                "url": "https://example.com/ep.mp3",
                "elapsed": 300.0,
                "duration": 3600.0,
            }

            from plugins.podcast import _on_player_status

            event = MagicMock()
            event.player_id = "aa:bb:cc:dd:ee:ff"
            event.state = "stopped"
            event.elapsed_seconds = 310.0
            event.duration = 3600.0
            event.current_track = {
                "source": "podcast",
                "path": "https://example.com/ep.mp3",
            }

            await _on_player_status(event)

            # Should have saved the resume position
            mock_store.set_resume_position.assert_called()
            call_args = mock_store.set_resume_position.call_args
            assert call_args[0][0] == "https://example.com/ep.mp3"
            assert call_args[0][1] == 310

            # Should have removed tracking
            assert "aa:bb:cc:dd:ee:ff" not in podcast_mod._player_tracking
        finally:
            podcast_mod._store = old_store
            podcast_mod._player_tracking.clear()
            podcast_mod._player_tracking.update(old_tracking)

    @pytest.mark.asyncio
    async def test_player_status_non_podcast_ignored(self):
        import plugins.podcast as podcast_mod

        old_store = podcast_mod._store
        old_tracking = dict(podcast_mod._player_tracking)
        try:
            mock_store = MagicMock()
            podcast_mod._store = mock_store

            from plugins.podcast import _on_player_status

            event = MagicMock()
            event.player_id = "aa:bb:cc:dd:ee:ff"
            event.state = "playing"
            event.elapsed_seconds = 120.0
            event.duration = 300.0
            event.current_track = {
                "source": "radio",
                "path": "https://radio.example.com/stream",
            }

            await _on_player_status(event)

            # Should NOT be tracking a radio stream
            assert "aa:bb:cc:dd:ee:ff" not in podcast_mod._player_tracking
        finally:
            podcast_mod._store = old_store
            podcast_mod._player_tracking.clear()
            podcast_mod._player_tracking.update(old_tracking)


# =============================================================================
# v2 feature tests — Store config updates
# =============================================================================


class TestStoreConfigUpdates:
    def test_update_max_recent(self, tmp_path: Path):
        store = PodcastStore(tmp_path, max_recent=50)
        for i in range(30):
            store.record_played(url=f"https://example.com/ep{i}.mp3", title=f"Ep {i}")
        assert store.recent_count == 30

        store.update_max_recent(20)
        assert store.recent_count == 20

    def test_update_auto_mark_played_percent(self, tmp_path: Path):
        store = PodcastStore(tmp_path, auto_mark_played_percent=90)
        store.update_auto_mark_played_percent(50)
        # Now episodes at 50% should be marked played
        store.update_progress("https://example.com/ep.mp3", 500, 1000)
        assert store.is_played("https://example.com/ep.mp3")

    def test_custom_resume_threshold(self, tmp_path: Path):
        store = PodcastStore(tmp_path, resume_threshold=30)
        store.set_resume_position("https://example.com/ep.mp3", 25)
        assert store.get_resume_position("https://example.com/ep.mp3") == 0
        store.set_resume_position("https://example.com/ep.mp3", 31)
        assert store.get_resume_position("https://example.com/ep.mp3") == 31
