"""FranklinWH aGate emulator: Modbus TCP in front of a closed-loop site model."""

from .clock import ManualClock, ScaledClock, SystemClock
from .gateway import Fleet, Gateway, GatewayConfig, mock_serial
from .profiles import PROFILES, Profile, get_profile
from .server import Faults
from .site import Site, SiteConfig

__all__ = ["Fleet", "Gateway", "GatewayConfig", "mock_serial", "Faults", "Site", "SiteConfig",
           "Profile", "PROFILES", "get_profile", "ManualClock", "ScaledClock", "SystemClock"]
# Between releases main is "<next>.devN", so a build after a tag never reports the tag (#163).
# A release sets the plain version; the commit after it bumps to the next .dev0.
__version__ = "0.2.2.dev0"
