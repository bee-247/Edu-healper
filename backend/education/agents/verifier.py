"""Verifier Agent for teacher-facing generated materials."""

from education.agents.base import BASE_PROMPT, TeacherAgentSpec


def build_spec() -> TeacherAgentSpec:
    return TeacherAgentSpec(
        name="verifier",
        description="Checks generated teacher materials for evidence, source ids, and review safety.",
        system_prompt=(
            BASE_PROMPT
            + "You are the Verifier Agent. Check whether generated questions, lesson plans, or grading references "
            "are supported by provided textbook evidence, include source_chunk_ids when needed, and keep teacher "
            "review visible for grading. Be concise and actionable."
        ),
        tools=[],
    )
