"""Price table schema: what a model call costs, per million tokens, when the provider did not
report a cost (task P2.2).

Prices are external facts that change, so they are **data you supply and date**, never
constants in the code. Rates are US dollars per million tokens. An unknown model has no cost,
which is reported as unknown and never as zero.

Token fields follow the audit event (``Usage``): ``tokens_in`` are input tokens that were not
served from a cache, ``tokens_cached`` are input tokens that were, and ``tokens_out`` are
output tokens. Cost is ``in * input + cached * cached_input + out * output``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from .common import SCHEMA_VERSION, StrictModel

MILLION = 1_000_000


class Rate(StrictModel):
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cached_input_per_mtok: float | None = Field(
        default=None,
        ge=0,
        description="Rate for input served from a cache. Unset means cached tokens are billed "
        "at the full input rate, which overstates and so errs on the safe side.",
    )


class PriceTable(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    as_of: date = Field(description="When these rates were copied from the provider")
    source: str = Field(min_length=1, description="Where the rates came from, for example a URL")
    models: dict[str, Rate] = Field(min_length=1)

    def cost(
        self, model: str, tokens_in: int, tokens_out: int, tokens_cached: int = 0
    ) -> float | None:
        """Cost in US dollars, or ``None`` when the model is not in the table."""
        rate = self.models.get(model)
        if rate is None:
            return None
        cached_rate = (
            rate.cached_input_per_mtok
            if rate.cached_input_per_mtok is not None
            else rate.input_per_mtok
        )
        total = (
            tokens_in * rate.input_per_mtok
            + tokens_cached * cached_rate
            + tokens_out * rate.output_per_mtok
        )
        return total / MILLION
