"""Resolve an organizer-hosted audio player without running its HTML or scripts."""

import asyncio
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


# Exact official DEV hosts. New organizer hosts require explicit review.
ALLOWED_AUDIO_HOSTS = frozenset({
    "ec88f21f1accecf38e20095a9fe175.a5.environment.api.powerplatform.com",
    "freedomholdingcorporation.crm11.dynamics.com",
})
MAX_HTML_BYTES = 256_000
MAX_REDIRECTS = 3
TOTAL_TIMEOUT_SECONDS = 20


class AudioSourceError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.status_code = status_code


def _safe_url(value: str) -> str:
    try:
        if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_AUDIO_HOSTS
                or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)):
            raise ValueError
    except ValueError:
        raise AudioSourceError("Источник аудио не входит в разрешённый список организаторов.") from None
    return value


class _AudioParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.sources: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "audio":
            self.depth += 1
        if tag == "audio" or (tag == "source" and self.depth):
            source = dict(attrs).get("src")
            if source and source.strip():
                self.sources.append(source.strip())

    def handle_endtag(self, tag):
        if tag == "audio":
            self.depth = max(0, self.depth - 1)


def _embedded_audio(content: bytes, base_url: str) -> str:
    parser = _AudioParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    sources = list(dict.fromkeys(urljoin(base_url, source) for source in parser.sources))
    if len(sources) != 1:
        raise AudioSourceError("Страница не содержит единственный доступный источник аудио.")
    return _safe_url(sources[0])


async def resolve_audio(source: str | None, *, client: httpx.AsyncClient | None = None) -> dict:
    """Read only bounded HTML, or return a direct audio response without downloading it.

    Callers supply the stored conversation's URL, never an arbitrary request URL.
    Returned signed links belong only in the playback response, not logs or errors.
    """
    if not source:
        raise AudioSourceError("У диалога нет аудиозаписи.", 404)
    current = _safe_url(source)
    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
    try:
        return await asyncio.wait_for(_fetch_audio(http, current), timeout=TOTAL_TIMEOUT_SECONDS)
    except (httpx.TimeoutException, TimeoutError):
        raise AudioSourceError("Сервис аудио не ответил вовремя. Повторите попытку.", 504) from None
    except httpx.HTTPError:
        raise AudioSourceError("Не удалось связаться с сервисом аудио.", 502) from None
    finally:
        if owned_client:
            await http.aclose()


async def _fetch_audio(http: httpx.AsyncClient, current: str) -> dict:
    for attempt in range(MAX_REDIRECTS + 1):
        async with http.stream("GET", current, follow_redirects=False,
                               headers={"Accept": "audio/*, text/html;q=0.9", "Accept-Encoding": "identity"}) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location or attempt == MAX_REDIRECTS:
                    raise AudioSourceError("Не удалось получить аудио: слишком много перенаправлений.", 502)
                current = _safe_url(urljoin(current, location))
                continue
            if response.status_code != 200:
                raise AudioSourceError("Сервис аудио недоступен или ссылка истекла.", 502)
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type.startswith("audio/") or media_type == "application/ogg":
                return {"audio_url": current, "resolved_from": "direct"}
            if media_type not in {"text/html", "application/xhtml+xml"}:
                raise AudioSourceError("Источник вернул неподдерживаемый формат аудио.")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise AudioSourceError("Страница аудио превышает допустимый размер.")
                chunks.append(chunk)
            return {"audio_url": _embedded_audio(b"".join(chunks), current), "resolved_from": "html"}
    raise AudioSourceError("Не удалось получить источник аудио.", 502)
