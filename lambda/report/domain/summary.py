"""Claude API summary result types and the token / spend record for a call."""

from dataclasses import dataclass
from typing import TypedDict

from config import CONFIG


class Repo(TypedDict):
    name: str
    summary: list[str]
    tags: list[str]


class Report(TypedDict):
    repositories: list[Repo]


@dataclass(frozen=True)
class Usage:
    """Token counts and USD spend for a single Claude API call.

    `spend_usd` is computed locally from the active model's configured rates,
    not returned by the Anthropic API.
    """

    input_tokens: int
    output_tokens: int
    spend_usd: float

    @classmethod
    def from_call(cls, input_tokens: int, output_tokens: int) -> "Usage":
        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        spend_usd = (
            input_tokens * rates["input_usd_per_1m_tokens"]
            + output_tokens * rates["output_usd_per_1m_tokens"]
        ) / 1_000_000
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            spend_usd=spend_usd,
        )
