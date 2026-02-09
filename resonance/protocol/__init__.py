"""
Protocol implementations for Resonance.

This package contains the network protocol handlers:
- slimproto: The Squeezebox binary protocol (port 3483)
- cli: The text-based CLI protocol (port 9090)
- discovery: UDP discovery for finding players
"""

from resonance.protocol.cli import CliServer
from resonance.protocol.slimproto import SlimprotoServer

__all__ = ["SlimprotoServer", "CliServer"]

