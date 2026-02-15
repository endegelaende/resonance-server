"""
Tests for the streaming transcoder module.

Tests cover:
- Parsing of legacy.conf rules
- Rule matching logic
- Command building
- Binary resolution

Also includes lightweight tests for streaming decision policy to ensure
format-handling behavior stays consistent.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from resonance.streaming.transcoder import (
    TranscodeConfig,
    TranscodeRule,
    build_command,
    parse_legacy_conf,
    resolve_binary,
)


class TestTranscodeRule:
    """Tests for TranscodeRule class."""

    def test_is_passthrough_with_dash(self) -> None:
        """Passthrough rule has '-' as command."""
        rule = TranscodeRule(
            source_format="mp3",
            dest_format="mp3",
            device_type="*",
            device_id="*",
            command="-",
        )
        assert rule.is_passthrough() is True

    def test_is_passthrough_with_command(self) -> None:
        """Non-passthrough rule has actual command."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $FILE$",
        )
        assert rule.is_passthrough() is False

    def test_matches_source_format(self) -> None:
        """Rule matches source format."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $FILE$",
        )
        assert rule.matches("m4b") is True
        assert rule.matches("M4B") is True  # Case insensitive
        assert rule.matches("mp3") is False

    def test_matches_device_type_wildcard(self) -> None:
        """Wildcard device type matches anything."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $FILE$",
        )
        assert rule.matches("m4b", device_type="boom") is True
        assert rule.matches("m4b", device_type="squeezebox") is True

    def test_matches_specific_device_type(self) -> None:
        """Specific device type only matches that device."""
        rule = TranscodeRule(
            source_format="wma",
            dest_format="mp3",
            device_type="slimp3",
            device_id="*",
            command="[wmadec] -w $FILE$",
        )
        assert rule.matches("wma", device_type="slimp3") is True
        assert rule.matches("wma", device_type="boom") is False

    def test_matches_device_id_wildcard(self) -> None:
        """Wildcard device ID matches any MAC."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $FILE$",
        )
        assert rule.matches("m4b", device_id="00:11:22:33:44:55") is True

    def test_matches_specific_device_id(self) -> None:
        """Specific device ID only matches that MAC."""
        rule = TranscodeRule(
            source_format="mp3",
            dest_format="mp3",
            device_type="*",
            device_id="00:11:22:33:44:55",
            command="[lame] --abr 128 $FILE$",
        )
        assert rule.matches("mp3", device_id="00:11:22:33:44:55") is True
        assert rule.matches("mp3", device_id="aa:bb:cc:dd:ee:ff") is False


class TestTranscodeConfig:
    """Tests for TranscodeConfig class."""

    def test_find_rule_matches_first(self) -> None:
        """find_rule returns the first matching rule."""
        rules = [
            TranscodeRule("m4b", "flc", "boom", "*", "[faad] specific"),
            TranscodeRule("m4b", "flc", "*", "*", "[faad] generic"),
        ]
        config = TranscodeConfig(rules=rules)

        # Specific device type should match first rule
        rule = config.find_rule("m4b", device_type="boom")
        assert rule is not None
        assert rule.command == "[faad] specific"

        # Other device should match second rule
        rule = config.find_rule("m4b", device_type="squeezebox")
        assert rule is not None
        assert rule.command == "[faad] generic"

    def test_find_rule_with_dest_format(self) -> None:
        """find_rule can filter by destination format."""
        rules = [
            TranscodeRule("m4b", "flc", "*", "*", "[faad] to flac"),
            TranscodeRule("m4b", "pcm", "*", "*", "[faad] to pcm"),
        ]
        config = TranscodeConfig(rules=rules)

        rule = config.find_rule("m4b", dest_format="pcm")
        assert rule is not None
        assert rule.dest_format == "pcm"

    def test_find_rule_no_match(self) -> None:
        """find_rule returns None if no rule matches."""
        rules = [
            TranscodeRule("mp3", "mp3", "*", "*", "-"),
        ]
        config = TranscodeConfig(rules=rules)

        rule = config.find_rule("m4b")
        assert rule is None

    def test_needs_transcoding_passthrough(self) -> None:
        """Passthrough rules don't need transcoding."""
        rules = [
            TranscodeRule("mp3", "mp3", "*", "*", "-"),
        ]
        config = TranscodeConfig(rules=rules)

        assert config.needs_transcoding("mp3") is False

    def test_needs_transcoding_with_rule(self) -> None:
        """Non-passthrough rules need transcoding."""
        rules = [
            TranscodeRule("m4b", "flc", "*", "*", "[faad] -q $FILE$"),
        ]
        config = TranscodeConfig(rules=rules)

        assert config.needs_transcoding("m4b") is True

    def test_needs_transcoding_no_rule(self) -> None:
        """Unknown formats need transcoding by default (safe fallback)."""
        config = TranscodeConfig(rules=[])

        assert config.needs_transcoding("unknown_format") is True


class TestParseLegacyConf:
    """Tests for parsing legacy.conf files."""

    def test_parse_simple_rule(self) -> None:
        """Parse a simple transcoding rule."""
        config_content = textwrap.dedent("""
            # Comment line
            m4b flc * *
            \t[faad] -q -w -f 1 $FILE$
        """)

        with NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = parse_legacy_conf(Path(f.name))

        assert len(config.rules) == 1
        rule = config.rules[0]
        assert rule.source_format == "m4b"
        assert rule.dest_format == "flc"
        assert rule.device_type == "*"
        assert rule.device_id == "*"
        assert "[faad]" in rule.command

    def test_parse_passthrough_rule(self) -> None:
        """Parse a passthrough rule."""
        config_content = textwrap.dedent("""
            mp3 mp3 * *
            \t-
        """)

        with NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = parse_legacy_conf(Path(f.name))

        assert len(config.rules) == 1
        assert config.rules[0].is_passthrough() is True

    def test_parse_multiple_rules(self) -> None:
        """Parse multiple rules."""
        config_content = textwrap.dedent("""
            m4b flc * *
            \t[faad] -q -w -f 1 $FILE$

            mp3 mp3 * *
            \t-

            wma mp3 slimp3 *
            \t[wmadec] -w $FILE$
        """)

        with NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = parse_legacy_conf(Path(f.name))

        assert len(config.rules) == 3

    def test_parse_with_capabilities(self) -> None:
        """Parse rule with capability flags."""
        config_content = textwrap.dedent("""
            m4b flc * *
            \t# FT
            \t[faad] -q -w -f 1 $FILE$
        """)

        with NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = parse_legacy_conf(Path(f.name))

        assert len(config.rules) == 1
        assert config.rules[0].capabilities == "FT"

    def test_parse_pipeline_command(self) -> None:
        """Parse a piped command."""
        config_content = textwrap.dedent("""
            m4b flc * *
            \t[faad] -q -w -f 1 $FILE$ | [flac] -cs -
        """)

        with NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = parse_legacy_conf(Path(f.name))

        assert len(config.rules) == 1
        assert "|" in config.rules[0].command


class TestBuildCommand:
    """Tests for building command lines."""

    def test_build_simple_command(self) -> None:
        """Build a simple command with file substitution."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="pcm",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 2 $FILE$",
        )

        file_path = Path("/music/audiobook.m4b")

        # Mock resolve_binary to return a path
        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            commands = build_command(rule, file_path)
            assert len(commands) == 1
            assert "faad" in commands[0][0]  # Path separator varies by OS
            assert "-q" in commands[0]
            assert str(file_path) in commands[0]
        finally:
            transcoder_module.resolve_binary = original_resolve

    def test_build_pipeline_command(self) -> None:
        """Build a piped command."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $FILE$ | [flac] -cs -",
        )

        file_path = Path("/music/audiobook.m4b")

        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            commands = build_command(rule, file_path)
            assert len(commands) == 2
            assert "faad" in commands[0][0]
            assert "flac" in commands[1][0]
        finally:
            transcoder_module.resolve_binary = original_resolve

    def test_build_command_binary_not_found(self) -> None:
        """Raise error if binary not found."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[nonexistent_binary] $FILE$",
        )

        file_path = Path("/music/audiobook.m4b")

        with pytest.raises(ValueError, match="Binary not found"):
            build_command(rule, file_path)

    def test_build_command_with_seek_start(self) -> None:
        """Build command with $START$ placeholder for seeking."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $START$ $END$ $FILE$",
        )

        file_path = Path("/music/audiobook.m4b")

        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            # With start_seconds=120.5, should insert -j 120.500
            commands = build_command(rule, file_path, start_seconds=120.5)
            assert len(commands) == 1
            cmd = commands[0]
            assert "-j" in cmd
            assert "120.500" in cmd
            assert str(file_path) in cmd
            # $END$ should be removed (empty) when not specified
            assert "-e" not in cmd
        finally:
            transcoder_module.resolve_binary = original_resolve

    def test_build_command_with_seek_start_and_end(self) -> None:
        """Build command with both $START$ and $END$ placeholders."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $START$ $END$ $FILE$",
        )

        file_path = Path("/music/audiobook.m4b")

        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            # With both start and end
            commands = build_command(rule, file_path, start_seconds=60.0, end_seconds=180.0)
            assert len(commands) == 1
            cmd = commands[0]
            assert "-j" in cmd
            assert "60.000" in cmd
            assert "-e" in cmd
            assert "180.000" in cmd
        finally:
            transcoder_module.resolve_binary = original_resolve

    def test_build_command_without_seek(self) -> None:
        """Build command without seek parameters removes placeholders cleanly."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $START$ $END$ $FILE$",
        )

        file_path = Path("/music/audiobook.m4b")

        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            # Without seek parameters, $START$ and $END$ should be removed
            commands = build_command(rule, file_path)
            assert len(commands) == 1
            cmd = commands[0]
            assert "-j" not in cmd
            assert "-e" not in cmd
            assert "$START$" not in " ".join(cmd)
            assert "$END$" not in " ".join(cmd)
            assert str(file_path) in cmd
        finally:
            transcoder_module.resolve_binary = original_resolve

    def test_build_command_seek_without_end_no_dash_e(self) -> None:
        """Regular seek must NOT produce -e flag (see SESSION_CONTEXT rule 4.6).

        LMS only substitutes $END$ for cuesheets (where end comes from the
        track path "#start-end").  For normal seeks, $END$ is left
        unsubstituted and cleaned up.  The LMS-patched faad interprets -e
        as a duration from seek point, so passing the full track duration
        produces garbage output.

        Correct:   faad -q -w -f 1 -j 1800.000 audiobook.m4b
        Wrong:     faad -q -w -f 1 -j 1800.000 -e 3600.000 audiobook.m4b
        """
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="mp3",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $START$ $END$ $FILE$ | [lame] --silent -q 2 - -",
        )

        file_path = Path("/music/audiobook.m4b")

        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            # Regular seek: only start, NO end — matches LMS behavior
            commands = build_command(rule, file_path, start_seconds=1800.0, end_seconds=None)
            flat = [arg for cmd in commands for arg in cmd]
            assert "-j" in flat
            assert "1800.000" in flat
            assert "-e" not in flat, (
                "Regular seek must not pass -e to faad; "
                "only cuesheets set end_seconds (LMS $END$ / capability U)"
            )
        finally:
            transcoder_module.resolve_binary = original_resolve

    def test_build_command_seek_zero_ignored(self) -> None:
        """Seek of 0 seconds should not add -j flag."""
        rule = TranscodeRule(
            source_format="m4b",
            dest_format="flc",
            device_type="*",
            device_id="*",
            command="[faad] -q -w -f 1 $START$ $END$ $FILE$",
        )

        file_path = Path("/music/audiobook.m4b")

        import resonance.streaming.transcoder as transcoder_module

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve

        try:
            # start_seconds=0 should not add -j
            commands = build_command(rule, file_path, start_seconds=0.0)
            assert len(commands) == 1
            cmd = commands[0]
            assert "-j" not in cmd
        finally:
            transcoder_module.resolve_binary = original_resolve


class TestResolveBinary:
    """Tests for binary resolution."""

    def test_resolve_binary_returns_none_for_nonexistent(self) -> None:
        """Returns None for non-existent binaries."""
        result = resolve_binary("this_binary_definitely_does_not_exist_12345")
        assert result is None

    def test_resolve_binary_finds_system_binary(self) -> None:
        """Finds binaries in system PATH."""
        # python should always be available
        result = resolve_binary("python")
        # May or may not be found depending on environment
        # Just verify it returns Path or None
        assert result is None or isinstance(result, Path)

    def test_resolve_binary_non_windows_ignores_exe_in_third_party(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Linux/macOS must not select Windows *.exe binaries from third_party/bin."""
        import resonance.streaming.transcoder as transcoder_module

        if transcoder_module.sys.platform.startswith("win"):
            pytest.skip("non-Windows specific behavior")

        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()
        (fake_bin_dir / "faad.exe").write_bytes(b"not a linux binary")

        monkeypatch.setattr(transcoder_module, "THIRD_PARTY_BIN", fake_bin_dir)
        monkeypatch.setattr(
            transcoder_module.shutil,
            "which",
            lambda name: "/usr/local/bin/faad" if name == "faad" else None,
        )

        result = resolve_binary("faad")
        assert result == Path("/usr/local/bin/faad")

    def test_resolve_binary_non_windows_prefers_executable_third_party_binary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Linux/macOS should prefer an executable third_party/bin binary over PATH."""
        import resonance.streaming.transcoder as transcoder_module

        if transcoder_module.sys.platform.startswith("win"):
            pytest.skip("non-Windows specific behavior")

        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()
        local_binary = fake_bin_dir / "faad"
        local_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        local_binary.chmod(0o755)

        monkeypatch.setattr(transcoder_module, "THIRD_PARTY_BIN", fake_bin_dir)
        monkeypatch.setattr(
            transcoder_module.shutil,
            "which",
            lambda name: "/usr/local/bin/faad" if name == "faad" else None,
        )

        result = resolve_binary("faad")
        assert result == local_binary


class TestStreamingDecisionLogic:
    """Tests for the shared streaming decision logic (policy module)."""

    def test_mp4_formats_always_need_transcoding(self) -> None:
        """MP4 container formats should always require transcoding."""
        from resonance.streaming.policy import needs_transcoding

        # These formats have HTTP streaming/container issues - always transcode
        for fmt in ["m4a", "m4b", "mp4", "m4p", "m4r", "alac", "aac"]:
            assert needs_transcoding(fmt, None) is True, f"{fmt} should need transcoding"
            assert needs_transcoding(fmt, "squeezeslave") is True, (
                f"{fmt} should need transcoding for modern device"
            )
            assert needs_transcoding(fmt, "boom") is True, (
                f"{fmt} should need transcoding for legacy device"
            )

    def test_strm_hint_matches_transcode_decision(self) -> None:
        """
        If a format is transcoded for streaming, the `strm` hint must signal the
        transcoded output format (currently FLAC) so the player expects the right data.
        """
        from resonance.streaming.policy import (
            DEFAULT_POLICY,
            needs_transcoding,
            strm_expected_format_hint,
        )

        # A few representative device types (string names are accepted)
        device_types = [None, "squeezeslave", "boom"]

        # When transcoding is needed, strm hint must be the transcode target (flac)
        transcode_formats = ["m4a", "m4b", "mp4", "alac", "aac"]
        for device in device_types:
            for fmt in transcode_formats:
                assert needs_transcoding(fmt, device) is True
                assert (
                    strm_expected_format_hint(fmt, device) == DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT
                )

        # When no transcoding is needed, strm hint must remain the normalized input format
        direct_formats = ["mp3", "flac", "ogg", "wav", "aiff", "aif"]
        for device in device_types:
            for fmt in direct_formats:
                assert needs_transcoding(fmt, device) is False
                assert strm_expected_format_hint(fmt, device) == fmt

    def test_native_formats_dont_need_transcoding(self) -> None:
        """Native streaming formats should not require transcoding."""
        from resonance.streaming.policy import needs_transcoding

        # These formats stream reliably over HTTP
        for fmt in ["mp3", "flac", "flc", "ogg", "wav", "aiff", "aif"]:
            assert needs_transcoding(fmt, None) is False, f"{fmt} should NOT need transcoding"
            assert needs_transcoding(fmt, "squeezeslave") is False, (
                f"{fmt} should NOT need transcoding for modern"
            )
            assert needs_transcoding(fmt, "boom") is False, (
                f"{fmt} should NOT need transcoding for legacy"
            )

    def test_case_insensitive_format_check(self) -> None:
        """Format checking should be case insensitive."""
        from resonance.streaming.policy import needs_transcoding

        assert needs_transcoding("M4B", None) is True
        assert needs_transcoding("FLAC", None) is False
        assert needs_transcoding("Mp3", None) is False

    def test_alac_always_needs_transcoding(self) -> None:
        """ALAC (Apple Lossless in MP4 container) must always be transcoded."""
        from resonance.streaming.policy import (
            DEFAULT_POLICY,
            needs_transcoding,
            strm_expected_format_hint,
        )

        for device in [None, "squeezeslave", "boom", "baby", "fab4", "squeezebox2"]:
            assert needs_transcoding("alac", device) is True, (
                f"alac should need transcoding for device={device}"
            )
            assert strm_expected_format_hint("alac", device) == DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT, (
                f"alac strm hint should be '{DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT}' for device={device}"
            )

    def test_opus_always_needs_transcoding(self) -> None:
        """Opus must always be transcoded — no Squeezebox player decodes it natively via HTTP."""
        from resonance.streaming.policy import (
            DEFAULT_POLICY,
            is_always_transcode_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_always_transcode_format("opus") is True

        for device in [None, "squeezeslave", "boom", "baby", "fab4"]:
            assert needs_transcoding("opus", device) is True, (
                f"opus should need transcoding for device={device}"
            )
            assert strm_expected_format_hint("opus", device) == DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT, (
                f"opus strm hint should be '{DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT}' for device={device}"
            )

    def test_wma_is_native_stream_format(self) -> None:
        """WMA should be in NATIVE_STREAM_FORMATS (SB2+ decode it natively)."""
        from resonance.streaming.policy import (
            is_native_stream_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_native_stream_format("wma") is True

        for device in [None, "squeezeslave", "boom", "fab4"]:
            assert needs_transcoding("wma", device) is False, (
                f"wma should NOT need transcoding for device={device}"
            )
            assert strm_expected_format_hint("wma", device) == "wma", (
                f"wma strm hint should remain 'wma' for device={device}"
            )

    def test_wma_and_opus_deterministic(self) -> None:
        """WMA and Opus transcoding decisions must be deterministic (not depend on device config fallback)."""
        from resonance.streaming.policy import (
            is_always_transcode_format,
            is_native_stream_format,
        )

        # Both formats are now explicitly listed in policy — no ambiguity.
        assert is_native_stream_format("wma") is True
        assert is_always_transcode_format("opus") is True

        # Neither should fall through to the device config fallback path.
        assert is_always_transcode_format("wma") is False
        assert is_native_stream_format("opus") is False

    # ------------------------------------------------------------------
    # WavPack / APE / MPC policy tests
    # ------------------------------------------------------------------

    def test_wavpack_always_needs_transcoding(self) -> None:
        """WavPack (.wv) must always be transcoded — no Squeezebox player decodes it natively via HTTP."""
        from resonance.streaming.policy import (
            DEFAULT_POLICY,
            is_always_transcode_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_always_transcode_format("wv") is True

        for device in [None, "squeezeslave", "boom", "baby", "fab4"]:
            assert needs_transcoding("wv", device) is True, (
                f"wv should need transcoding for device={device}"
            )
            assert strm_expected_format_hint("wv", device) == DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT, (
                f"wv strm hint should be '{DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT}' for device={device}"
            )

    def test_ape_always_needs_transcoding(self) -> None:
        """Monkey's Audio (.ape) must always be transcoded."""
        from resonance.streaming.policy import (
            DEFAULT_POLICY,
            is_always_transcode_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_always_transcode_format("ape") is True

        for device in [None, "squeezeslave", "boom", "baby", "fab4"]:
            assert needs_transcoding("ape", device) is True, (
                f"ape should need transcoding for device={device}"
            )
            assert strm_expected_format_hint("ape", device) == DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT, (
                f"ape strm hint should be '{DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT}' for device={device}"
            )

    def test_mpc_always_needs_transcoding(self) -> None:
        """Musepack (.mpc) must always be transcoded."""
        from resonance.streaming.policy import (
            DEFAULT_POLICY,
            is_always_transcode_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_always_transcode_format("mpc") is True

        for device in [None, "squeezeslave", "boom", "baby", "fab4"]:
            assert needs_transcoding("mpc", device) is True, (
                f"mpc should need transcoding for device={device}"
            )
            assert strm_expected_format_hint("mpc", device) == DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT, (
                f"mpc strm hint should be '{DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT}' for device={device}"
            )

    def test_new_transcode_formats_not_native(self) -> None:
        """wv/ape/mpc must NOT be in NATIVE_STREAM_FORMATS."""
        from resonance.streaming.policy import is_native_stream_format

        for fmt in ["wv", "ape", "mpc"]:
            assert is_native_stream_format(fmt) is False, (
                f"{fmt} should NOT be a native stream format"
            )

    # ------------------------------------------------------------------
    # DSD (DSF/DFF) policy tests
    # ------------------------------------------------------------------

    def test_dsf_is_native_stream_format(self) -> None:
        """DSF should be in NATIVE_STREAM_FORMATS (LMS streams DSF as passthrough)."""
        from resonance.streaming.policy import (
            is_native_stream_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_native_stream_format("dsf") is True

        for device in [None, "squeezeslave", "boom", "baby", "fab4"]:
            assert needs_transcoding("dsf", device) is False, (
                f"dsf should NOT need transcoding for device={device}"
            )
            assert strm_expected_format_hint("dsf", device) == "dsf", (
                f"dsf strm hint should remain 'dsf' for device={device}"
            )

    def test_dff_is_native_stream_format(self) -> None:
        """DFF should be in NATIVE_STREAM_FORMATS (LMS streams DFF as passthrough)."""
        from resonance.streaming.policy import (
            is_native_stream_format,
            needs_transcoding,
            strm_expected_format_hint,
        )

        assert is_native_stream_format("dff") is True

        for device in [None, "squeezeslave", "boom", "baby", "fab4"]:
            assert needs_transcoding("dff", device) is False, (
                f"dff should NOT need transcoding for device={device}"
            )
            assert strm_expected_format_hint("dff", device) == "dff", (
                f"dff strm hint should remain 'dff' for device={device}"
            )

    def test_dsf_dff_not_in_always_transcode(self) -> None:
        """DSF/DFF must NOT be in ALWAYS_TRANSCODE_FORMATS (they are native passthrough)."""
        from resonance.streaming.policy import is_always_transcode_format

        assert is_always_transcode_format("dsf") is False
        assert is_always_transcode_format("dff") is False

    def test_dsf_dff_deterministic(self) -> None:
        """DSF/DFF transcoding decisions must be deterministic (no device config fallback)."""
        from resonance.streaming.policy import (
            is_always_transcode_format,
            is_native_stream_format,
        )

        # Both formats are explicitly listed in NATIVE_STREAM_FORMATS.
        assert is_native_stream_format("dsf") is True
        assert is_native_stream_format("dff") is True

        # Neither should be in always-transcode.
        assert is_always_transcode_format("dsf") is False
        assert is_always_transcode_format("dff") is False


class TestLegacyConfRuleMatching:
    """Tests that verify rule matching against the real legacy.conf file.

    These tests load the actual shipping legacy.conf and verify that the
    correct rules are found for each format — ensuring that the first
    matching rule aligns with TRANSCODE_TARGET_FORMAT ('mp3').
    """

    @pytest.fixture()
    def real_config(self) -> TranscodeConfig:
        """Load the real legacy.conf from the project."""
        conf_path = Path(__file__).parent.parent / "resonance" / "config" / "legacy.conf"
        assert conf_path.exists(), f"legacy.conf not found at {conf_path}"
        return parse_legacy_conf(conf_path)

    # -- Opus --

    def test_opus_first_rule_is_mp3(self, real_config: TranscodeConfig) -> None:
        """The first opus rule (no dest_format filter) must target mp3,
        matching TRANSCODE_TARGET_FORMAT for consistent strm signaling."""
        rule = real_config.find_rule("opus")
        assert rule is not None, "No rule found for opus"
        assert rule.dest_format == "mp3", (
            f"First opus rule should target mp3, got {rule.dest_format}"
        )
        assert not rule.is_passthrough()

    def test_opus_mp3_rule_uses_sox_and_lame(self, real_config: TranscodeConfig) -> None:
        """Opus → MP3 pipeline should use sox for decoding and lame for encoding."""
        rule = real_config.find_rule("opus", dest_format="mp3")
        assert rule is not None, "No opus→mp3 rule found"
        assert "[sox]" in rule.command, "opus→mp3 should use sox"
        assert "[lame]" in rule.command, "opus→mp3 should pipe through lame"
        assert "|" in rule.command, "opus→mp3 should be a pipeline"

    def test_opus_flc_rule_exists(self, real_config: TranscodeConfig) -> None:
        """Opus → FLAC fallback rule should exist."""
        rule = real_config.find_rule("opus", dest_format="flc")
        assert rule is not None, "No opus→flc rule found"
        assert "[sox]" in rule.command
        assert "flac" in rule.command

    def test_opus_pcm_rule_exists(self, real_config: TranscodeConfig) -> None:
        """Opus → PCM fallback rule should exist."""
        rule = real_config.find_rule("opus", dest_format="pcm")
        assert rule is not None, "No opus→pcm rule found"
        assert "[sox]" in rule.command

    def test_opus_mp3_command_builds(self, real_config: TranscodeConfig) -> None:
        """Opus → MP3 command should build without errors (with mocked binaries)."""
        import resonance.streaming.transcoder as transcoder_module

        rule = real_config.find_rule("opus", dest_format="mp3")
        assert rule is not None

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve
        try:
            commands = build_command(rule, Path("/music/podcast.opus"))
            assert len(commands) == 2, "opus→mp3 should be a 2-stage pipeline"
            # First stage: sox decoding
            assert "sox" in commands[0][0].lower()
            assert str(Path("/music/podcast.opus")) in " ".join(commands[0])
            # Second stage: lame encoding
            assert "lame" in commands[1][0].lower()
        finally:
            transcoder_module.resolve_binary = original_resolve

    # -- WavPack --

    def test_wavpack_first_rule_is_mp3(self, real_config: TranscodeConfig) -> None:
        """The first wv rule must target mp3."""
        rule = real_config.find_rule("wv")
        assert rule is not None, "No rule found for wv"
        assert rule.dest_format == "mp3", (
            f"First wv rule should target mp3, got {rule.dest_format}"
        )
        assert not rule.is_passthrough()

    def test_wavpack_mp3_rule_uses_ffmpeg_and_lame(self, real_config: TranscodeConfig) -> None:
        """WavPack → MP3 pipeline should use ffmpeg + lame."""
        rule = real_config.find_rule("wv", dest_format="mp3")
        assert rule is not None, "No wv→mp3 rule found"
        assert "[ffmpeg]" in rule.command
        assert "[lame]" in rule.command
        assert "|" in rule.command

    def test_wavpack_flc_rule_exists(self, real_config: TranscodeConfig) -> None:
        """WavPack → FLAC fallback rule should exist."""
        rule = real_config.find_rule("wv", dest_format="flc")
        assert rule is not None, "No wv→flc rule found"
        assert "[ffmpeg]" in rule.command
        assert "flac" in rule.command

    def test_wavpack_mp3_command_builds_with_seek(self, real_config: TranscodeConfig) -> None:
        """WavPack → MP3 command should build correctly, including seek substitution."""
        import resonance.streaming.transcoder as transcoder_module

        rule = real_config.find_rule("wv", dest_format="mp3")
        assert rule is not None

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve
        try:
            # Without seek
            commands = build_command(rule, Path("/music/track.wv"))
            assert len(commands) == 2
            assert "ffmpeg" in commands[0][0].lower()
            assert "lame" in commands[1][0].lower()
            # $START$ should be removed when no seek
            flat = " ".join(commands[0])
            assert "-ss" not in flat

            # With seek
            commands_seek = build_command(
                rule, Path("/music/track.wv"), start_seconds=42.5
            )
            flat_seek = " ".join(commands_seek[0])
            assert "-ss" in flat_seek, "ffmpeg should use -ss for seeking"
            assert "42.500" in flat_seek
        finally:
            transcoder_module.resolve_binary = original_resolve

    # -- APE (Monkey's Audio) --

    def test_ape_first_rule_is_mp3(self, real_config: TranscodeConfig) -> None:
        """The first ape rule must target mp3."""
        rule = real_config.find_rule("ape")
        assert rule is not None, "No rule found for ape"
        assert rule.dest_format == "mp3", (
            f"First ape rule should target mp3, got {rule.dest_format}"
        )

    def test_ape_mp3_rule_uses_ffmpeg_and_lame(self, real_config: TranscodeConfig) -> None:
        """APE → MP3 pipeline should use ffmpeg + lame."""
        rule = real_config.find_rule("ape", dest_format="mp3")
        assert rule is not None, "No ape→mp3 rule found"
        assert "[ffmpeg]" in rule.command
        assert "[lame]" in rule.command

    def test_ape_flc_rule_exists(self, real_config: TranscodeConfig) -> None:
        """APE → FLAC fallback rule should exist."""
        rule = real_config.find_rule("ape", dest_format="flc")
        assert rule is not None, "No ape→flc rule found"
        assert "[ffmpeg]" in rule.command

    def test_ape_mp3_command_builds(self, real_config: TranscodeConfig) -> None:
        """APE → MP3 command should build without errors."""
        import resonance.streaming.transcoder as transcoder_module

        rule = real_config.find_rule("ape", dest_format="mp3")
        assert rule is not None

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve
        try:
            commands = build_command(rule, Path("/music/track.ape"))
            assert len(commands) == 2
            assert "ffmpeg" in commands[0][0].lower()
            assert "lame" in commands[1][0].lower()
            assert str(Path("/music/track.ape")) in " ".join(commands[0])
        finally:
            transcoder_module.resolve_binary = original_resolve

    # -- Musepack --

    def test_mpc_first_rule_is_mp3(self, real_config: TranscodeConfig) -> None:
        """The first mpc rule must target mp3."""
        rule = real_config.find_rule("mpc")
        assert rule is not None, "No rule found for mpc"
        assert rule.dest_format == "mp3", (
            f"First mpc rule should target mp3, got {rule.dest_format}"
        )

    def test_mpc_mp3_rule_uses_ffmpeg_and_lame(self, real_config: TranscodeConfig) -> None:
        """MPC → MP3 pipeline should use ffmpeg + lame."""
        rule = real_config.find_rule("mpc", dest_format="mp3")
        assert rule is not None, "No mpc→mp3 rule found"
        assert "[ffmpeg]" in rule.command
        assert "[lame]" in rule.command

    def test_mpc_flc_rule_exists(self, real_config: TranscodeConfig) -> None:
        """MPC → FLAC fallback rule should exist."""
        rule = real_config.find_rule("mpc", dest_format="flc")
        assert rule is not None, "No mpc→flc rule found"
        assert "[ffmpeg]" in rule.command

    def test_mpc_mp3_command_builds(self, real_config: TranscodeConfig) -> None:
        """MPC → MP3 command should build without errors."""
        import resonance.streaming.transcoder as transcoder_module

        rule = real_config.find_rule("mpc", dest_format="mp3")
        assert rule is not None

        original_resolve = transcoder_module.resolve_binary

        def mock_resolve(name: str) -> Path | None:
            return Path(f"/usr/bin/{name}")

        transcoder_module.resolve_binary = mock_resolve
        try:
            commands = build_command(rule, Path("/music/track.mpc"))
            assert len(commands) == 2
            assert "ffmpeg" in commands[0][0].lower()
            assert "lame" in commands[1][0].lower()
            assert str(Path("/music/track.mpc")) in " ".join(commands[0])
        finally:
            transcoder_module.resolve_binary = original_resolve

    # -- Cross-cutting: first rule alignment with TRANSCODE_TARGET_FORMAT --

    def test_all_transcode_formats_first_rule_matches_target(
        self, real_config: TranscodeConfig
    ) -> None:
        """For every format in ALWAYS_TRANSCODE_FORMATS, the first legacy.conf
        rule (found without dest_format filter) must produce the format declared
        in TRANSCODE_TARGET_FORMAT. Otherwise the strm frame will mismatch the
        actual streamed content."""
        from resonance.streaming.policy import DEFAULT_POLICY

        target = DEFAULT_POLICY.TRANSCODE_TARGET_FORMAT

        # Formats that have transcode rules in legacy.conf
        # (aac is passthrough-capable for ADTS, m4p/m4r share mp4/m4a rules)
        formats_with_rules = ["m4a", "m4b", "mp4", "alac", "opus", "wv", "ape", "mpc"]

        for fmt in formats_with_rules:
            rule = real_config.find_rule(fmt)
            assert rule is not None, f"No rule found for {fmt}"
            assert rule.dest_format == target, (
                f"First rule for '{fmt}' targets '{rule.dest_format}', "
                f"expected '{target}' (TRANSCODE_TARGET_FORMAT)"
            )

    # ------------------------------------------------------------------
    # DSD (DSF/DFF) legacy.conf rule matching
    # ------------------------------------------------------------------

    def test_dsf_first_rule_is_passthrough(self, real_config: TranscodeConfig) -> None:
        """DSF first rule should be passthrough (dsf → dsf), matching LMS convert.conf."""
        rule = real_config.find_rule("dsf")
        assert rule is not None, "No rule found for dsf"
        assert rule.dest_format == "dsf"
        assert rule.is_passthrough() is True

    def test_dff_first_rule_is_passthrough(self, real_config: TranscodeConfig) -> None:
        """DFF first rule should be passthrough (dff → dff), matching LMS convert.conf."""
        rule = real_config.find_rule("dff")
        assert rule is not None, "No rule found for dff"
        assert rule.dest_format == "dff"
        assert rule.is_passthrough() is True

    def test_dsf_flc_transcode_rule_exists(self, real_config: TranscodeConfig) -> None:
        """DSF → FLAC transcode fallback rule should exist."""
        rule = real_config.find_rule("dsf", dest_format="flc")
        assert rule is not None, "No dsf→flc rule found"
        assert not rule.is_passthrough()
        assert "ffmpeg" in rule.command.lower()

    def test_dff_flc_transcode_rule_exists(self, real_config: TranscodeConfig) -> None:
        """DFF → FLAC transcode fallback rule should exist."""
        rule = real_config.find_rule("dff", dest_format="flc")
        assert rule is not None, "No dff→flc rule found"
        assert not rule.is_passthrough()
        assert "ffmpeg" in rule.command.lower()

    def test_dsf_mp3_transcode_rule_exists(self, real_config: TranscodeConfig) -> None:
        """DSF → MP3 transcode fallback rule should exist."""
        rule = real_config.find_rule("dsf", dest_format="mp3")
        assert rule is not None, "No dsf→mp3 rule found"
        assert not rule.is_passthrough()

    def test_dff_mp3_transcode_rule_exists(self, real_config: TranscodeConfig) -> None:
        """DFF → MP3 transcode fallback rule should exist."""
        rule = real_config.find_rule("dff", dest_format="mp3")
        assert rule is not None, "No dff→mp3 rule found"
        assert not rule.is_passthrough()

    def test_dsf_pcm_transcode_rule_exists(self, real_config: TranscodeConfig) -> None:
        """DSF → PCM transcode fallback rule should exist."""
        rule = real_config.find_rule("dsf", dest_format="pcm")
        assert rule is not None, "No dsf→pcm rule found"
        assert not rule.is_passthrough()

    def test_dff_pcm_transcode_rule_exists(self, real_config: TranscodeConfig) -> None:
        """DFF → PCM transcode fallback rule should exist."""
        rule = real_config.find_rule("dff", dest_format="pcm")
        assert rule is not None, "No dff→pcm rule found"
        assert not rule.is_passthrough()

    def test_dsf_mp3_command_builds(self, real_config: TranscodeConfig) -> None:
        """DSF → MP3 command should build correctly with ffmpeg + lame pipeline."""
        rule = real_config.find_rule("dsf", dest_format="mp3")
        assert rule is not None

        def mock_resolve(name: str) -> Path:
            return Path(f"/usr/bin/{name}")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("resonance.streaming.transcoder.resolve_binary", mock_resolve)
            commands = build_command(rule, Path("/music/test.dsf"))

        assert len(commands) == 2  # ffmpeg | lame pipeline
        assert commands[0][0] == str(Path("/usr/bin/ffmpeg"))
        assert str(Path("/music/test.dsf")) in commands[0]
        assert commands[1][0] == str(Path("/usr/bin/lame"))

    def test_dff_flc_command_builds(self, real_config: TranscodeConfig) -> None:
        """DFF → FLAC command should build correctly with ffmpeg."""
        rule = real_config.find_rule("dff", dest_format="flc")
        assert rule is not None

        def mock_resolve(name: str) -> Path:
            return Path(f"/usr/bin/{name}")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("resonance.streaming.transcoder.resolve_binary", mock_resolve)
            commands = build_command(rule, Path("/music/test.dff"))

        assert len(commands) == 1  # single ffmpeg command
        assert commands[0][0] == str(Path("/usr/bin/ffmpeg"))
        assert str(Path("/music/test.dff")) in commands[0]
        assert "-f" in commands[0]
        assert "flac" in commands[0]

    def test_dsf_mp3_command_builds_with_seek(self, real_config: TranscodeConfig) -> None:
        """DSF → MP3 with seek should insert -ss flag for ffmpeg."""
        rule = real_config.find_rule("dsf", dest_format="mp3")
        assert rule is not None

        def mock_resolve(name: str) -> Path:
            return Path(f"/usr/bin/{name}")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("resonance.streaming.transcoder.resolve_binary", mock_resolve)
            commands = build_command(
                rule,
                Path("/music/test.dsf"),
                start_seconds=30.0,
            )

        # ffmpeg uses -ss for seeking
        ffmpeg_args = commands[0]
        assert "-ss" in ffmpeg_args
        assert "30.000" in ffmpeg_args
