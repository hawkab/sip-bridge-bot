from dataclasses import dataclass


@dataclass
class ResponseItem:
    kind: str  # text | file | file_group
    text: str | None = None
    parse_mode: str | None = None
    attachment_path: str | None = None
    attachment_name: str | None = None
    caption: str | None = None
    attachment_paths: list[str] | None = None
    attachment_names: list[str | None] | None = None


@dataclass
class CommandResult:
    items: list[ResponseItem]
    post_action: str | None = None
