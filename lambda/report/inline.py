"""Inline notation in generated summaries, shared by the Notion and Slack renderers."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Code spans come first in the alternation so a run starting with a backtick is taken whole and its contents stay unparsed
_TOKEN = re.compile(
    r"`(?P<code>[^`]+)`"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*)?#(?P<number>\d+)"
)


@dataclass(frozen=True, slots=True)
class Segment:
    """One run of summary text sharing a single rendering style."""

    text: str
    code: bool = False
    bold: bool = False
    url: str | None = None


def issue_url(owner: str, repo: str, number: str) -> str:
    """Build the GitHub URL for a number reference.

    `/issues/` also covers pull requests because GitHub redirects to `/pull/` when the number belongs to one,
    so the reference needs no PR / issue classification.
    """
    return f"https://github.com/{owner}/{repo}/issues/{number}"


def parse_inline(text: str, owner: str, repo: str) -> list[Segment]:
    """Split `text` into styled runs on inline code, bold, and number references.

    Runs are taken left to right and never nest, so the markup inside a code span stays literal and a backtick inside a bold span does the same.
    Bare numbers resolve against `repo`; a number written with a repository name resolves against that one.
    """
    segments: list[Segment] = []
    pos = 0

    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            segments.append(Segment(text[pos : m.start()]))
        if m.group("code") is not None:
            segments.append(Segment(m.group("code"), code=True))
        elif m.group("bold") is not None:
            segments.append(Segment(m.group("bold"), bold=True))
        else:
            target = m.group("repo") or repo
            # Keep the reference as written so a cross-repository number stays readable
            segments.append(
                Segment(m.group(0), url=issue_url(owner, target, m.group("number")))
            )
        pos = m.end()

    if pos < len(text):
        segments.append(Segment(text[pos:]))

    return segments
