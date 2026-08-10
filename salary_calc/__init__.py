# -*- coding: utf-8 -*-
"""סימולטור שכר - מנוע חישוב מבוסס נתוני הסימולציות הרשמיים."""

from .engine import (
    Breakdown,
    Component,
    Dataset,
    Result,
    load_dataset,
)

__all__ = ["Breakdown", "Component", "Dataset", "Result", "load_dataset"]
__version__ = "1.0.0"
