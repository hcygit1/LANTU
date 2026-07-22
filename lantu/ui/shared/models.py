from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class ToolViewState:
    tool_id: str
    name: str
    arguments: dict[str, Any]
    status: ToolStatus = ToolStatus.RUNNING
    output: str = ""
    elapsed: float = 0.0


@dataclass
class LiveViewState:
    assistant_text: str = ""
    thinking_text: str = ""
    tools: dict[str, ToolViewState] = field(default_factory=dict)
    status_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
