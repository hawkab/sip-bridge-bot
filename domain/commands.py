from dataclasses import dataclass


@dataclass
class InboundCommand:
    source: str
    sender: str
    raw_command: str
