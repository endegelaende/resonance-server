"""Tests for JSON-RPC helper utilities."""

from __future__ import annotations

from resonance.web.jsonrpc_helpers import build_player_item, is_audio_player


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