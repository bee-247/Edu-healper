"""Registry for all specialized teacher Agents."""

from education.agents import general, grading_assistant, lesson_planner, question_generator, verifier
from education.agents.base import TeacherAgentSpec


def get_teacher_agent_specs() -> dict[str, TeacherAgentSpec]:
    specs = [
        general.build_spec(),
        question_generator.build_spec(),
        lesson_planner.build_spec(),
        grading_assistant.build_spec(),
        verifier.build_spec(),
    ]
    return {spec.name: spec for spec in specs}


def get_teacher_agent_tools() -> list:
    """Return a de-duplicated union of tools across all teacher Agents."""
    tools = []
    seen = set()
    for spec in get_teacher_agent_specs().values():
        for tool in spec.tools:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", repr(tool))
            if name in seen:
                continue
            seen.add(name)
            tools.append(tool)
    return tools
