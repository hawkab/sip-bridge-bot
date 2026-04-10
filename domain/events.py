from dataclasses import dataclass


@dataclass
class SMSReceivedEvent:
    sender: str
    sim: str
    received_at: str
    text: str


@dataclass
class CdrGroupEvent:
    rows: list[dict]
