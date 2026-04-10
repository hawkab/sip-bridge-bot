from enum import Enum


class ResponseItemKind(str, Enum):
    TEXT = "text"
    FILE = "file"


class DeliveryChannel(str, Enum):
    TELEGRAM = "telegram"
    EMAIL = "email"
