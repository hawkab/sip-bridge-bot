import html
import re


_ANCHOR_RE = re.compile(r"<a\s+href=(['\"])(.*?)\1>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"```(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*(.+?)\*")


class _PlaceholderStore:
    def __init__(self):
        self._items: list[str] = []

    def put(self, value: str) -> str:
        token = f"@@HTML_PART_{len(self._items)}@@"
        self._items.append(value)
        return token

    def restore(self, value: str) -> str:
        for index, item in enumerate(self._items):
            value = value.replace(f"@@HTML_PART_{index}@@", item)
        return value


def render_email_html(text: str) -> str:
    body = (text or "").strip()
    if not body:
        body = "Готово."

    store = _PlaceholderStore()
    body = _extract_anchors(body, store)
    body = html.escape(body)
    body = _extract_code_blocks(body, store)
    body = _extract_inline_code(body, store)
    body = _BOLD_RE.sub(r"<strong>\1</strong>", body)
    body = body.replace("\n", "<br>\n")
    body = store.restore(body)

    return (
        "<!doctype html>"
        "<html><body style=\"margin:0;padding:16px;font-family:Arial,Helvetica,sans-serif;line-height:1.5;\">"
        f"{body}"
        "</body></html>"
    )


def html_to_plain_text(text: str) -> str:
    if not text:
        return ""
    value = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</div\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _extract_anchors(value: str, store: _PlaceholderStore) -> str:
    def replace(match: re.Match[str]) -> str:
        href = html.escape(match.group(2), quote=True)
        label = html.escape(match.group(3))
        return store.put(f'<a href="{href}">{label}</a>')

    return _ANCHOR_RE.sub(replace, value)


def _extract_code_blocks(value: str, store: _PlaceholderStore) -> str:
    def replace(match: re.Match[str]) -> str:
        code = match.group(1).strip("\n")
        return store.put(
            "<pre style=\"margin:8px 0;padding:12px;border-radius:6px;background:#f5f5f5;"
            "white-space:pre-wrap;font-family:Consolas,Monaco,monospace;\">"
            f"{code}</pre>"
        )

    return _CODE_BLOCK_RE.sub(replace, value)


def _extract_inline_code(value: str, store: _PlaceholderStore) -> str:
    def replace(match: re.Match[str]) -> str:
        code = match.group(1)
        return store.put(
            "<code style=\"padding:2px 4px;border-radius:4px;background:#f5f5f5;"
            "font-family:Consolas,Monaco,monospace;\">"
            f"{code}</code>"
        )

    return _INLINE_CODE_RE.sub(replace, value)
