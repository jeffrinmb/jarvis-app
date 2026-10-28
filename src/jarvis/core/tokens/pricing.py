"""Load a price table (schema in ``jarvis.core.schemas.price_table``)."""

from __future__ import annotations

from pathlib import Path

from jarvis.core.schemas.loader import parse_yaml_model
from jarvis.core.schemas.price_table import PriceTable, Rate

__all__ = ["PriceTable", "Rate", "load_price_table"]


def load_price_table(path: str | Path) -> PriceTable:
    return parse_yaml_model(Path(path).read_text(encoding="utf-8"), PriceTable)
