"""Request models all the servers accept as tool parameters."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..conditions import validate_compare_target

MAX_COMPARISON_DELAY_MS = 3_600_000  # an event comparison may wait at most an hour for its end event


MAX_ALSO = 4  # extra conditions one question may add
StateOp = Literal["==", "!=", ">", ">=", "<", "<="]  # the comparisons that describe a state a topic is in


class Condition(BaseModel):
    """One more condition that must hold at the same time."""

    # Used as `also` on wpilog_when and nt4_wait_for. Descriptions go to the model in every tool schema, so keep them short.
    # A value holds until its topic's next update, so "mode == auto" is true for the whole autonomous period.
    topic: str = Field(min_length=1, description="Topic name")
    op: StateOp = Field(description="Comparison")
    value: Any = Field(None, description="Value to compare against")
    field: str | None = Field(None, description="Struct field")

    @model_validator(mode="after")
    def _check_value(self) -> "Condition":
        validate_compare_target(self.op, self.value)
        return self


class EventComparison(BaseModel):
    start_rule: int = Field(ge=0, description="Start rule index")
    end_rule: int = Field(ge=0, description="End rule index")
    max_delay_ms: float | None = Field(None, gt=0, le=MAX_COMPARISON_DELAY_MS, description="Max ms to wait for the end event")
    max_pairs: int = Field(20, ge=1, le=50, description="Max pairs to compare")
