"""Per-skill bridge entry (currently disabled)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from skill_runtime import SkillRunResult

if TYPE_CHECKING:
    from schemas import SendMessageRequest


def apply_skill_bridges(
    payload: SendMessageRequest,
    skill_results: list[SkillRunResult],
) -> tuple[SendMessageRequest, list[SkillRunResult]]:
    """No-op for now: keep payload and skill trace unchanged."""
    return payload, skill_results
