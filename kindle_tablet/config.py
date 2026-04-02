from __future__ import annotations

"""Configuration for kindle-tablet."""

from dataclasses import dataclass, field


@dataclass
class KindleConfig:
    host: str = "192.168.1.100"
    port: int = 2222
    username: str = "root"
    password: str = ""
    key_path: str = ""
    # TCP streaming port (used when running tablet-server on Kindle)
    stream_port: int = 8234


@dataclass
class TabletConfig:
    # Kindle Scribe digitizer resolution (will be auto-detected)
    kindle_max_x: int = 15725
    kindle_max_y: int = 20966
    kindle_max_pressure: int = 4095
    kindle_max_tilt: int = 90
    # Host screen area to map to (0.0 - 1.0 = full screen)
    screen_region: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    # Pressure curve: 1.0 = linear, <1 = softer, >1 = firmer
    pressure_curve: float = 0.7
    # Minimum pressure when pen is touching (prevents Krita ignoring light strokes)
    pressure_floor: float = 0.05
    # Whether to map tilt
    enable_tilt: bool = True
    # Whether to process touch input
    enable_touch: bool = True


@dataclass
class Config:
    kindle: KindleConfig = field(default_factory=KindleConfig)
    tablet: TabletConfig = field(default_factory=TabletConfig)
    # Connection mode: "ssh" (simple) or "tcp" (lower latency, needs server on kindle)
    mode: str = "ssh"
    # Event device paths on the Kindle (auto-detected if empty)
    pen_device: str = ""
    touch_device: str = ""
