"""IT-новости за день: двадцать ссылок с Hacker News, GitHub и Hugging Face, без пересказа.

Отбор — правилами: очки и звёзды внутри раздела, квота на каждый раздел. Что уже присылали, второй раз
не приходит (bot/repo/news.py). Почему так, а не LLM или отдельный крон, — docs/adr/0002.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiosqlite import Connection

from bot.repo import news as news_repo

log = logging.getLogger(__name__)

LIMIT = 20
# Раздел и сколько ссылок он даёт. Недобор (источник молчит, всё уже было) добирается в этом же порядке.
QUOTAS = {"top": 6, "show": 4, "repo": 4, "model": 4, "release": 2}
REPOS = ("anthropics/claude-code", "aiogram/aiogram", "astral-sh/uv", "astral-sh/ruff")
# С запасом: история со вчерашнего вечера успевает набрать очки, а повторы всё равно отсекает news_sent.
HN_WINDOW = timedelta(days=2)
NEW_REPO_WINDOW = timedelta(days=7)
RELEASE_WINDOW = timedelta(days=7)
TIMEOUT = aiohttp.ClientTimeout(total=20)
HEADERS = {"User-Agent": "personal-tg-bot"}  # без User-Agent GitHub отвечает 403

Fetch = Callable[[str], Awaitable[Any]]
# Одна подборка за раз: двойное нажатие «Прислать сейчас» или нажатие рядом с вечерним тиком иначе прочитали бы
# news_sent до того, как первая подборка туда записана, и прислали бы одно и то же дважды.
LOCK = asyncio.Lock()


@dataclass(frozen=True)
class Item:
    kind: str  # ключ из QUOTAS
    title: str
    url: str
    note: str = ""
    note_url: str | None = None


def url_key(url: str) -> str:
    """Один и тот же адрес с HN, GitHub и из прошлых дней — один ключ."""
    return url.strip().split("://", 1)[-1].removeprefix("www.").rstrip("/")


def short_count(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


async def hacker_news(fetch: Fetch, now: datetime, tag: str) -> list[Item]:
    since = int((now - HN_WINDOW).timestamp())
    query = urlencode({"tags": tag, "numericFilters": f"created_at_i>{since}", "hitsPerPage": 50})
    data = await fetch(f"https://hn.algolia.com/api/v1/search?{query}")
    kind = "show" if tag == "show_hn" else "top"
    items = []
    for hit in sorted(data["hits"], key=lambda hit: hit.get("points") or 0, reverse=True):
        discussion = f"https://news.ycombinator.com/item?id={hit['objectID']}"
        title = hit["title"].removeprefix("Show HN: ")
        items.append(Item(kind, title, hit.get("url") or discussion, f"{hit.get('points') or 0} ↑", discussion))
    return items


async def github_new(fetch: Fetch, now: datetime) -> list[Item]:
    since = (now - NEW_REPO_WINDOW).date().isoformat()
    query = urlencode({"q": f"created:>{since}", "sort": "stars", "order": "desc", "per_page": 20})
    data = await fetch(f"https://api.github.com/search/repositories?{query}")
    return [
        Item(
            "repo",
            repo["full_name"] + (f" — {repo['description']}" if repo.get("description") else ""),
            repo["html_url"],
            f"★ {short_count(repo['stargazers_count'])}",
        )
        for repo in data["items"]
    ]


async def hugging_face(fetch: Fetch, now: datetime) -> list[Item]:
    data = await fetch("https://huggingface.co/api/models?sort=trendingScore&limit=20")
    return [
        Item(
            "model",
            model["id"],
            f"https://huggingface.co/{model['id']}",
            " · ".join(filter(None, [model.get("pipeline_tag"), f"♥ {short_count(model.get('likes') or 0)}"])),
        )
        for model in data
    ]


async def releases(fetch: Fetch, now: datetime) -> list[Item]:
    async def latest(repo: str) -> tuple[str, Any]:
        return repo, await fetch(f"https://api.github.com/repos/{repo}/releases/latest")

    fresh = []
    for result in await asyncio.gather(*(latest(repo) for repo in REPOS), return_exceptions=True):
        if isinstance(result, BaseException):
            log.warning("Новости: релиз не получен: %r", result)
            continue
        repo, release = result
        published = datetime.fromisoformat(release["published_at"])
        if now - published <= RELEASE_WINDOW:
            title = f"{repo.split('/')[1]} {release['tag_name']}"
            fresh.append((published, Item("release", title, release["html_url"])))
    # Сначала тот, что ждёт дольше: частые релизы одного репозитория не заслоняют редкие у других.
    return [item for _, item in sorted(fresh, key=lambda pair: pair[0])]


SOURCES = {
    "top": partial(hacker_news, tag="story"),
    "show": partial(hacker_news, tag="show_hn"),
    "repo": github_new,
    "model": hugging_face,
    "release": releases,
}


async def collect(fetch: Fetch, now: datetime) -> dict[str, list[Item]]:
    """Кандидаты по разделам, лучшие первыми. Источник, который не ответил, даёт пустой раздел."""

    async def safe(kind: str) -> list[Item]:
        try:
            return await SOURCES[kind](fetch, now)
        except Exception as error:  # чужой API: таймаут, 403, другая форма ответа
            log.warning("Новости: источник %s не ответил: %r", kind, error)
            return []

    results = await asyncio.gather(*(safe(kind) for kind in SOURCES))
    # Ссылка не на http(s) сломала бы всё сообщение: Telegram отклоняет такую разметку целиком.
    return {
        kind: [item for item in items if item.url.startswith(("https://", "http://"))]
        for kind, items in zip(SOURCES, results, strict=True)
    }


def pick(candidates: dict[str, list[Item]], sent: set[str], limit: int = LIMIT) -> list[Item]:
    """Квота на раздел, недобор — по порядку разделов. Присланное раньше и повторы между разделами пропускаем."""
    seen = set(sent)
    chosen: list[Item] = []

    def take(kind: str, count: int) -> None:
        for item in candidates.get(kind, []):
            if count <= 0:
                return
            key = url_key(item.url)
            if key not in seen:
                seen.add(key)
                chosen.append(item)
                count -= 1

    for kind, quota in QUOTAS.items():
        take(kind, quota)
    for kind in QUOTAS:
        take(kind, limit - len(chosen))
    order = list(QUOTAS)
    return sorted(chosen, key=lambda item: order.index(item.kind))


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def fetch_candidates(now: datetime) -> dict[str, list[Item]]:
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=HEADERS) as session:
        return await collect(partial(_fetch_json, session), now)


async def deliver(db: Connection, now: datetime, send: Callable[[list[Item]], Awaitable[bool]]) -> bool:
    """Собирает подборку и отдаёт её send; пустая — источники молчат или всё свежее уже было.
    send вернул True — подборка ушла и помечается присланной."""
    async with LOCK:
        items = pick(await fetch_candidates(now), await news_repo.sent_keys(db))
        if not await send(items):
            return False
        await news_repo.remember(db, [url_key(item.url) for item in items], now)
        return True
