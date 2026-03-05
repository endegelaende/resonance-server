"""Tests for JSON-RPC helper utilities."""

from __future__ import annotations

from resonance.web.jsonrpc_helpers import (
    build_player_item,
    is_audio_player,
    parse_start_count,
    parse_tagged_params,
)


class _DeviceType:
    def __init__(self, name: str) -> None:
        self.name = name


class _Info:
    def __init__(
        self,
        device_name: str,
        *,
        model: str = "",
        capabilities: dict[str, str] | None = None,
    ) -> None:
        self.device_type = _DeviceType(device_name)
        self.model = model
        self.capabilities = capabilities or {}


class _Player:
    def __init__(
        self,
        mac: str,
        name: str,
        device_name: str,
        *,
        model: str = "",
        capabilities: dict[str, str] | None = None,
    ) -> None:
        self.mac_address = mac
        self.name = name
        self.info = _Info(device_name, model=model, capabilities=capabilities)


class _PlayerWithOverride(_Player):
    def __init__(self, mac: str, name: str, device_name: str, is_player: bool) -> None:
        super().__init__(mac, name, device_name)
        self.is_player = is_player


def test_build_player_item_marks_controller_as_non_audio_player() -> None:
    player = _Player("00:11:22:33:44:55", "Controller", "CONTROLLER")

    item = build_player_item(player)

    assert item["model"] == "controller"
    assert item["displaytype"] == "controller"
    assert item["isplayer"] == 0


def test_build_player_item_marks_regular_player_as_audio_player() -> None:
    player = _Player("aa:bb:cc:dd:ee:ff", "Kitchen", "SQUEEZEPLAY")

    item = build_player_item(player)

    assert item["model"] == "squeezeplay"
    assert item["isplayer"] == 1


def test_is_audio_player_prefers_explicit_override_false() -> None:
    player = _PlayerWithOverride(
        "aa:bb:cc:dd:ee:ff",
        "Manual Override",
        "SQUEEZEPLAY",
        is_player=False,
    )

    assert is_audio_player(player) is False


def test_is_audio_player_prefers_explicit_override_true() -> None:
    player = _PlayerWithOverride(
        "aa:bb:cc:dd:ee:ff",
        "Manual Override",
        "CONTROLLER",
        is_player=True,
    )

    assert is_audio_player(player) is True


def test_build_player_item_falls_back_from_nil_name_to_model_label() -> None:
    player = _Player("00:04:20:26:84:ae", "nil", "CONTROLLER")

    item = build_player_item(player)

    assert item["name"] == "Squeezebox Controller"


def test_build_player_item_prefers_capability_model_name_when_name_nil() -> None:
    player = _Player(
        "00:04:20:26:84:ae",
        "nil",
        "CONTROLLER",
        capabilities={"Model": "baby", "ModelName": "Squeezebox Radio"},
    )

    item = build_player_item(player)

    assert item["name"] == "Squeezebox Radio"
    assert item["model"] == "baby"
    assert item["displaytype"] == "baby"
    assert item["isplayer"] == 1


def test_build_player_item_keeps_real_name() -> None:
    player = _Player("00:04:20:26:84:ae", "Wohnzimmer", "CONTROLLER")

    item = build_player_item(player)

    assert item["name"] == "Wohnzimmer"


# =============================================================================
# parse_tagged_params
# =============================================================================


class TestParseTaggedParams:
    """Tests for parse_tagged_params (including dict support for Cometd)."""

    def test_empty_list(self) -> None:
        assert parse_tagged_params([]) == {}

    def test_colon_separated_strings(self) -> None:
        result = parse_tagged_params(["menu:1", "item_id:5"])
        assert result == {"menu": "1", "item_id": "5"}

    def test_colon_in_value_preserved(self) -> None:
        result = parse_tagged_params(["url:http://example.com:8080/stream"])
        assert result == {"url": "http://example.com:8080/stream"}

    def test_dict_elements_from_cometd(self) -> None:
        result = parse_tagged_params([{"menu": "1", "item_id": "5"}])
        assert result == {"menu": "1", "item_id": "5"}

    def test_dict_skips_none_values(self) -> None:
        result = parse_tagged_params([{"menu": "1", "empty": None}])
        assert result == {"menu": "1"}

    def test_dict_values_converted_to_str(self) -> None:
        result = parse_tagged_params([{"count": 42, "flag": True}])
        assert result == {"count": "42", "flag": "True"}

    def test_mixed_strings_and_dicts(self) -> None:
        result = parse_tagged_params(["menu:1", {"item_id": "5"}, "search:jazz"])
        assert result == {"menu": "1", "item_id": "5", "search": "jazz"}

    def test_non_string_non_dict_ignored(self) -> None:
        result = parse_tagged_params([42, 3.14, True, "menu:1"])
        assert result == {"menu": "1"}

    def test_string_without_colon_ignored(self) -> None:
        result = parse_tagged_params(["novalue", "menu:1"])
        assert result == {"menu": "1"}

    def test_later_values_overwrite_earlier(self) -> None:
        result = parse_tagged_params(["key:first", {"key": "second"}])
        assert result == {"key": "second"}


# =============================================================================
# parse_start_count
# =============================================================================


class TestParseStartCount:
    """Tests for parse_start_count (plugin sub-command pagination)."""

    def test_defaults_when_no_positional_args(self) -> None:
        start, count = parse_start_count(["favorites", "items"])
        assert start == 0
        assert count == 200

    def test_parses_start_and_count(self) -> None:
        start, count = parse_start_count(["favorites", "items", 10, 50])
        assert start == 10
        assert count == 50

    def test_string_numbers_parsed(self) -> None:
        start, count = parse_start_count(["favorites", "items", "20", "100"])
        assert start == 20
        assert count == 100

    def test_custom_sub_offset(self) -> None:
        start, count = parse_start_count(["podcast", "search", "jazz", 0, 25], sub_offset=3)
        assert start == 0
        assert count == 25

    def test_negative_start_clamped_to_zero(self) -> None:
        start, count = parse_start_count(["cmd", "sub", -5, 50])
        assert start == 0

    def test_negative_count_clamped_to_zero(self) -> None:
        start, count = parse_start_count(["cmd", "sub", 0, -10])
        assert count == 0

    def test_count_clamped_to_max(self) -> None:
        start, count = parse_start_count(["cmd", "sub", 0, 999999])
        assert count == 10_000

    def test_start_clamped_to_max(self) -> None:
        start, count = parse_start_count(["cmd", "sub", 9999999, 10])
        assert start == 1_000_000

    def test_invalid_values_use_defaults(self) -> None:
        start, count = parse_start_count(["cmd", "sub", "abc", "xyz"])
        assert start == 0
        assert count == 200

    def test_only_start_provided(self) -> None:
        start, count = parse_start_count(["cmd", "sub", 5])
        assert start == 5
        assert count == 200

    def test_tagged_params_after_pagination_ignored(self) -> None:
        start, count = parse_start_count(["radio", "items", 0, 100, "menu:1", "category:pop"])
        assert start == 0
        assert count == 100
