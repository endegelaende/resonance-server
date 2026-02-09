"""
Device capabilities for Squeezebox hardware and software players.

Each device type has different hardware features (line-in, digital out,
tone controls) and uses a different volume curve. LMS handles this via
a class hierarchy (Squeezebox2.pm, Boom.pm, SqueezePlay.pm, etc.).
Resonance uses a data-driven approach instead: a frozen dataclass per
device type, looked up from a simple dict.

Volume curve parameters are ported directly from the LMS Perl source:
  - Squeezebox2.pm  -> getVolumeParameters()
  - Boom.pm         -> getVolumeParameters()
  - SqueezePlay.pm  -> getVolumeParameters()  (identical to Boom)

Reference: Slim/Player/Client.pm for the full list of capability defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

from resonance.player.client import DeviceType

# ---------------------------------------------------------------------------
# Volume curve parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VolumeParameters:
    """Parameters for the LMS logarithmic volume curve.

    LMS uses a two-slope dB ramp.  ``step_point`` divides the 0-100 range
    into a lower and upper region with different slopes.  A negative
    ``step_point`` disables the lower slope (simple linear ramp in dB).

    Attributes:
        total_volume_range_db: Full range in dB (e.g. -50 or -74).
        step_point: UI position (0-100) where the slope changes.
            Negative means single slope only.
        step_fraction: Fraction of total_volume_range_db at the step point.
    """

    total_volume_range_db: float
    step_point: float
    step_fraction: float


# Pre-built parameter sets matching LMS exactly.
SB2_VOLUME = VolumeParameters(
    total_volume_range_db=-50.0,
    step_point=-1.0,
    step_fraction=1.0,
)
"""Squeezebox2/3, Transporter, Receiver: simple -50 dB ramp."""

BOOM_VOLUME = VolumeParameters(
    total_volume_range_db=-74.0,
    step_point=25.0,
    step_fraction=0.5,
)
"""Boom, SqueezePlay (Radio/Touch): two-slope curve for built-in speakers.

The step_point at 25 with step_fraction 0.5 means:
  - UI 0-25  covers -74 dB to -37 dB  (steep, fine control at low volume)
  - UI 25-100 covers -37 dB to  0 dB  (gentler slope for normal listening)
This makes 50% volume feel "reasonable" on devices with built-in speakers.
"""


# ---------------------------------------------------------------------------
# Device capabilities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceCapabilities:
    """Hardware capabilities and audio parameters for a device type.

    All boolean flags and range values match the defaults from
    Slim/Player/Client.pm with per-device overrides from the
    respective Player/*.pm modules.

    Note: max_bass == min_bass (e.g. both 50) means the device does NOT
    support bass adjustment.  Only when they differ is the setting shown.
    Same logic applies to treble and XL.
    """

    volume_params: VolumeParameters

    # Hardware features
    has_line_in: bool = False
    has_digital_out: bool = False
    has_digital_in: bool = False
    has_head_sub_out: bool = False
    has_balance: bool = False
    has_rtc_alarm: bool = False
    has_pre_amp: bool = False
    has_disable_dac: bool = False
    has_polarity_inversion: bool = False
    can_power_off: bool = True

    # Tone control ranges (equal values = not adjustable)
    max_bass: int = 50
    min_bass: int = 50
    max_treble: int = 50
    min_treble: int = 50
    max_xl: int = 0
    min_xl: int = 0

    @property
    def has_bass(self) -> bool:
        return self.max_bass != self.min_bass

    @property
    def has_treble(self) -> bool:
        return self.max_treble != self.min_treble

    @property
    def has_stereo_xl(self) -> bool:
        return self.max_xl != self.min_xl


# ---------------------------------------------------------------------------
# Per-device capability definitions
# ---------------------------------------------------------------------------
# Source: LMS Slim/Player/*.pm — only capabilities that differ from the
# Client.pm defaults are specified explicitly.

_SQUEEZEBOX2 = DeviceCapabilities(
    volume_params=SB2_VOLUME,
    has_balance=True,
    has_pre_amp=True,
    has_disable_dac=True,
)

_TRANSPORTER = DeviceCapabilities(
    volume_params=SB2_VOLUME,
    has_digital_in=True,
    has_digital_out=True,
    has_balance=True,
    has_polarity_inversion=True,
    can_power_off=True,
)

_RECEIVER = DeviceCapabilities(
    volume_params=SB2_VOLUME,
)

_BOOM = DeviceCapabilities(
    volume_params=BOOM_VOLUME,
    has_line_in=True,
    has_head_sub_out=True,
    has_rtc_alarm=True,
    can_power_off=False,
    max_bass=23,
    min_bass=-23,
    max_treble=23,
    min_treble=-23,
    max_xl=3,
    min_xl=0,
)

_SQUEEZEPLAY = DeviceCapabilities(
    volume_params=BOOM_VOLUME,
    has_balance=True,
)

_DEFAULT = DeviceCapabilities(
    volume_params=SB2_VOLUME,
)

# Mapping from DeviceType enum to capabilities.
DEVICE_CAPABILITIES: dict[DeviceType, DeviceCapabilities] = {
    DeviceType.SQUEEZEBOX: _DEFAULT,
    DeviceType.SQUEEZEBOX2: _SQUEEZEBOX2,
    DeviceType.TRANSPORTER: _TRANSPORTER,
    DeviceType.RECEIVER: _RECEIVER,
    DeviceType.BOOM: _BOOM,
    DeviceType.SOFTBOOM: _BOOM,
    DeviceType.SQUEEZEPLAY: _SQUEEZEPLAY,
    DeviceType.CONTROLLER: _SQUEEZEPLAY,
    DeviceType.SQUEEZESLAVE: _DEFAULT,
    DeviceType.SOFTSQUEEZE: _DEFAULT,
    DeviceType.SOFTSQUEEZE3: _DEFAULT,
    DeviceType.SLIMP3: _DEFAULT,
}


def get_device_capabilities(device_type: DeviceType) -> DeviceCapabilities:
    """Look up capabilities for a device type.

    Returns the default (SB2 volume curve) for unknown device types.
    """
    return DEVICE_CAPABILITIES.get(device_type, _DEFAULT)
