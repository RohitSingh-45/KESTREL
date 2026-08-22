"""Process-wide overrides that an admin can change without restarting.

DATA_MODE is normally an environment variable. The admin panel can flip it for
the running process so a demo can switch between live, hybrid and synthetic
without a redeploy. The override is not persisted across restarts.
"""

from __future__ import annotations

from typing import Literal

from app.config import settings

DataMode = Literal["live", "synthetic", "hybrid"]

_data_mode: DataMode | None = None


def get_data_mode() -> DataMode:
    return _data_mode or settings.data_mode


def set_data_mode(mode: DataMode) -> DataMode:
    global _data_mode
    _data_mode = mode
    return _data_mode


def reset_data_mode() -> DataMode:
    global _data_mode
    _data_mode = None
    return settings.data_mode
