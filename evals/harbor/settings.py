"""Settings shared by the Harbor adapter and lightweight tests."""

VALID_REASONING_EFFORTS = {"low", "medium", "high"}


def resolve_reasoning_effort(value: str | None) -> str:
    """Resolve the task-specific reasoning level from the host environment."""
    effort = (value or "low").strip().lower()
    if effort not in VALID_REASONING_EFFORTS:
        choices = ", ".join(sorted(VALID_REASONING_EFFORTS))
        raise ValueError(
            f"LANTU_REASONING_EFFORT must be one of: {choices}; got {effort!r}"
        )
    return effort
