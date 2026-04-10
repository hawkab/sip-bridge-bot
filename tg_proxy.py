import asyncio
import html
import json
import logging
import os
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

SUPPORTED_PROXY_SCHEMES = ("http://", "https://", "socks5://")


def _mask_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "direct"
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname or "?"
        port = f":{parsed.port}" if parsed.port else ""
        user = parsed.username or ""
        auth = f"{user}:***@" if user else ""
        return f"{parsed.scheme}://{auth}{host}{port}"
    except Exception:
        return proxy_url


def _normalize_proxy_line(line: str, default_scheme: str = "http") -> str | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None

    value = html.unescape(value)
    if value.startswith(("tg://proxy?", "mtproto://")):
        logger.debug("Skip unsupported MTProto proxy entry: %s", value)
        return None

    if value.startswith(SUPPORTED_PROXY_SCHEMES):
        return value

    if value.lower().startswith("socks5 "):
        value = value.split(None, 1)[1].strip()
        default_scheme = "socks5"

    if re.fullmatch(r"[^\s:]+:\d+", value):
        return f"{default_scheme}://{value}"

    parts = value.split()
    if len(parts) == 2 and re.fullmatch(r"\d+", parts[1]):
        return f"{default_scheme}://{parts[0]}:{parts[1]}"

    return None


def _unique(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def load_proxy_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    proxies = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            proxy = _normalize_proxy_line(line, default_scheme="http")
            if proxy:
                proxies.append(proxy)
    except Exception:
        logger.exception("Failed to read proxy file: %s", path)
        return []
    return _unique(proxies)


def save_proxy_file(path: Path, proxies: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_unique(proxies)) + ("\n" if proxies else ""), encoding="utf-8")


def save_mtproto_entries(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        try:
            existing = [line.rstrip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            existing = []
    merged = _unique(existing + entries)
    path.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")


async def probe_telegram(token: str, proxy_url: str | None, timeout: float) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/getMe"
    client_kwargs = {
        "timeout": httpx.Timeout(timeout),
        "follow_redirects": False,
        "trust_env": False,
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(url)
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        payload = response.json()
        if payload.get("ok") is True:
            return True, payload.get("result", {}).get("username", "") or "ok"
        return False, json.dumps(payload, ensure_ascii=False)[:400]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def _download_text(url: str, timeout: float) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True, trust_env=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _extract_proxies_from_text(text: str, default_scheme: str) -> list[str]:
    proxies = []
    for line in text.splitlines():
        proxy = _normalize_proxy_line(line, default_scheme=default_scheme)
        if proxy:
            proxies.append(proxy)
    return _unique(proxies)


async def _download_github_proxies(urls: list[str], timeout: float) -> list[str]:
    collected = []
    for url in urls:
        try:
            text = await _download_text(url, timeout)
            default_scheme = "socks5" if "socks" in url.lower() else "http"
            proxies = _extract_proxies_from_text(text, default_scheme=default_scheme)
            logger.info("Downloaded %s usable proxies from %s", len(proxies), url)
            collected.extend(proxies)
        except Exception as exc:
            logger.warning("Failed to download proxies from %s: %s", url, exc)
    return _unique(collected)


async def choose_working_proxy(config) -> str | None:
    success, details = await probe_telegram(config.BOT_TOKEN, None, config.TG_PROXY_TEST_TIMEOUT)
    if success:
        logger.info("Telegram Bot API is reachable without proxy (%s)", details)
        return None

    logger.warning("Direct Telegram connection failed: %s", details)

    local_proxies = load_proxy_file(config.TG_PROXY_FILE)
    if local_proxies:
        selected = await _try_proxy_candidates(config, local_proxies, source=f"file {config.TG_PROXY_FILE}")
        if selected:
            return selected
    else:
        logger.info("Proxy file is empty or absent: %s", config.TG_PROXY_FILE)

    github_proxies = await _download_github_proxies(config.TG_PROXY_GITHUB_URLS, config.TG_PROXY_TEST_TIMEOUT)
    if github_proxies:
        save_proxy_file(config.TG_PROXY_FILE, github_proxies)
        selected = await _try_proxy_candidates(config, github_proxies, source="GitHub")
        if selected:
            return selected

    raise RuntimeError(
        "Unable to reach Telegram Bot API directly or via supported proxies. "
        "MTProto entries from mtproto.ru are not usable in the current Bot API stack."
    )


async def _try_proxy_candidates(config, proxies: list[str], source: str) -> str | None:
    logger.info("Trying %s proxy candidates from %s", len(proxies), source)
    for index, proxy in enumerate(proxies, start=1):
        masked = _mask_proxy(proxy)
        ok, details = await probe_telegram(config.BOT_TOKEN, proxy, config.TG_PROXY_TEST_TIMEOUT)
        if ok:
            logger.info("Telegram connection succeeded via proxy #%s from %s: %s (%s)", index, source, masked, details)
            return proxy
        logger.warning("Telegram connection failed via proxy #%s from %s: %s (%s)", index, source, masked, details)
    return None


def apply_runtime_proxy_env(proxy_url: str | None) -> None:
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    if proxy_url:
        scheme = urlparse(proxy_url).scheme.lower()
        if scheme in {"http", "https"}:
            for key in keys:
                os.environ[key] = proxy_url
            return

    for key in keys:
        os.environ.pop(key, None)
