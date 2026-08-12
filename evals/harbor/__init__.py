"""Harbor integrations for evaluating LANTU."""

__all__ = ["LantuAgent"]


def __getattr__(name: str):
    if name == "LantuAgent":
        from .lantu_agent import LantuAgent

        return LantuAgent
    raise AttributeError(name)
