"""Вечерние IT-новости: сбор из источников, отбор десяти ссылок, рассылка без повторов."""

import asyncio
import html
import re
from datetime import timedelta

import aiohttp
import aiosqlite
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from bot import db as database
from bot.callbacks import SetCb
from bot.repo import news as news_repo
from bot.repo import settings
from bot.services import news, scheduler
from bot.services import timeutil as tu
from bot.services.news import Item
from bot.views import news as news_views
from tests.conftest import NOW, OWNER, TZ

EVENING = NOW + timedelta(hours=12)  # 17 октября, 20:00 по Алматы


def _hit(object_id: int, title: str, points: int, url: str | None) -> dict:
    return {"objectID": str(object_id), "title": title, "points": points, "url": url}


def _release(tag: str, published: str) -> dict:
    return {"tag_name": tag, "published_at": published, "html_url": f"https://github.com/r/{tag}"}


RESPONSES = {
    "tags=story": {
        "hits": [
            _hit(1, "Small", 50, "https://a.dev/post"),
            _hit(2, "Ask HN: Text post", 300, None),
            _hit(3, "Odd link", 900, "ftp://files.example"),
        ]
    },
    "tags=show_hn": {"hits": [_hit(4, "Show HN: Tool", 20, "https://tool.dev")]},
    "search/repositories": {
        "items": [
            {
                "full_name": "x/fast",
                "description": "Fast thing",
                "html_url": "https://github.com/x/fast",
                "stargazers_count": 2100,
            },
            {
                "full_name": "x/bare",
                "description": None,
                "html_url": "https://github.com/x/bare",
                "stargazers_count": 999,
            },
        ]
    },
    "huggingface.co": aiohttp.ClientConnectionError("нет связи"),
    "anthropics/claude-code/releases": _release("v2.1.0", "2026-10-16T10:00:00Z"),
    "astral-sh/uv/releases": _release("0.13.0", "2026-10-14T10:00:00Z"),
    "astral-sh/ruff/releases": _release("0.17.0", "2026-09-01T10:00:00Z"),
    "aiogram/aiogram/releases": TimeoutError(),
}


async def fake_fetch(url: str):
    for marker, answer in RESPONSES.items():
        if marker in url:
            if isinstance(answer, Exception):
                raise answer
            return answer
    raise AssertionError(f"Неожиданный адрес: {url}")


async def test_collect_ranks_each_source_and_survives_broken_ones():
    found = await news.collect(fake_fetch, NOW)

    # Лучшие по очкам первыми; текстовый пост ведёт на обсуждение; ссылка не на http(s) выброшена.
    assert [(i.title, i.url, i.note) for i in found["top"]] == [
        ("Ask HN: Text post", "https://news.ycombinator.com/item?id=2", "300 ↑"),
        ("Small", "https://a.dev/post", "50 ↑"),
    ]
    assert found["top"][1].note_url == "https://news.ycombinator.com/item?id=1"
    assert [i.title for i in found["show"]] == ["Tool"]
    assert [(i.title, i.note) for i in found["repo"]] == [("x/fast — Fast thing", "★ 2.1k"), ("x/bare", "★ 999")]
    assert found["model"] == []  # Hugging Face не ответил — остальные разделы на месте
    # Месячный релиз ruff устарел, aiogram не ответил; дольше ждущий релиз — первым.
    assert [i.title for i in found["release"]] == ["uv 0.13.0", "claude-code v2.1.0"]


def _items(kind: str, *names: str) -> list[Item]:
    return [Item(kind, name, f"https://{name}.dev") for name in names]


def test_pick_keeps_quotas_skips_repeats_and_fills_up():
    candidates = {
        "top": _items("top", *(f"t{i}" for i in range(10))),
        "show": [Item("show", "t0 again", "http://www.t0.dev/"), *_items("show", "s1", "s2", "s3", "s4", "s5")],
        "repo": [],
        "model": _items("model", "m1", "m2", "m3", "m4", "m5"),
        "release": _items("release", "r1", "r2", "r3"),
    }
    picked = news.pick(candidates, sent={news.url_key("https://t1.dev")})
    # По квоте 6 + 4 + 0 + 4 + 2 = 16: t1 уже присылали, «t0 again» — тот же адрес. Недобор 4 —
    # сначала остаток HN (t7–t9), потом следующий раздел (s5).
    assert [i.title for i in picked] == [
        "t0", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
        "s1", "s2", "s3", "s4", "s5",
        "m1", "m2", "m3", "m4",
        "r1", "r2",
    ]  # fmt: skip


def test_digest_of_longest_items_fits_into_one_message():
    long = Item("model", "x" * 300, "https://huggingface.co/" + "y" * 200, "image-text-to-text · ♥ 999.9k")
    text = news_views.digest_text([long] * news.LIMIT, NOW.date())
    visible = html.unescape(re.sub(r"<[^>]+>", "", text))  # Telegram считает 4096 символов без разметки
    assert len(visible) <= 4096


def test_digest_text_escapes_titles_and_links():
    items = [
        Item("top", "a < b", 'https://x.dev/?a=1&b="2"', "5 ↑", "https://news.ycombinator.com/item?id=7"),
        Item("release", "uv 0.13.0", "https://github.com/astral-sh/uv/releases/tag/0.13.0"),
    ]
    text = news_views.digest_text(items, NOW.date())
    assert text.startswith("📰 <b>IT за 17 октября</b>")
    assert '🔥 <a href="https://x.dev/?a=1&amp;b=&quot;2&quot;">a &lt; b</a>' in text
    assert '· <a href="https://news.ycombinator.com/item?id=7">5 ↑</a>' in text
    assert text.endswith('📦 <a href="https://github.com/astral-sh/uv/releases/tag/0.13.0">uv 0.13.0</a>')


async def test_evening_news_once_a_day_without_repeats(db, user, fake_bot, monkeypatch):
    await settings.update(db, OWNER, news_on=1)
    candidates = {"top": _items("top", "first")}

    async def fetched(now):
        return candidates

    monkeypatch.setattr(news, "fetch_candidates", fetched)
    await scheduler.tick(fake_bot, db, EVENING - timedelta(minutes=1), OWNER)
    assert fake_bot.sent == []

    await scheduler.tick(fake_bot, db, EVENING, OWNER)
    [(chat_id, text, _)] = fake_bot.sent
    assert chat_id == OWNER and "IT за 17 октября" in text and 'href="https://first.dev"' in text
    assert fake_bot.link_previews[-1].is_disabled  # 20 ссылок без превью первой
    assert (await settings.get(db, OWNER))["news_next_at"] == "2026-10-18 15:00:00"

    await scheduler.tick(fake_bot, db, EVENING + timedelta(hours=1), OWNER)
    assert len(fake_bot.sent) == 1

    candidates["top"] = _items("top", "first", "second")
    await scheduler.tick(fake_bot, db, EVENING + timedelta(days=1), OWNER)
    assert "second.dev" in fake_bot.sent[-1][1] and "first.dev" not in fake_bot.sent[-1][1]
    assert await news_repo.sent_keys(db) == {"first.dev", "second.dev"}


async def test_evening_news_says_when_sources_are_silent(db, user, fake_bot, monkeypatch):
    await settings.update(db, OWNER, news_on=1)

    async def silent(now):
        return {kind: [] for kind in news.QUOTAS}

    monkeypatch.setattr(news, "fetch_candidates", silent)
    await scheduler.tick(fake_bot, db, EVENING, OWNER)
    assert [text for _, text, _ in fake_bot.sent] == [news_views.EMPTY]
    assert (await settings.get(db, OWNER))["news_next_at"] == "2026-10-18 15:00:00"


async def test_evening_news_retries_in_ten_minutes_when_telegram_is_down(db, user, fake_bot, monkeypatch):
    await settings.update(db, OWNER, news_on=1)
    fetches = []

    async def fetched(now):
        fetches.append(now)
        return {"top": _items("top", "first")}

    async def telegram_down(*args, **kwargs):
        raise TelegramNetworkError(method=SendMessage(chat_id=OWNER, text="…"), message="нет связи")

    monkeypatch.setattr(news, "fetch_candidates", fetched)
    monkeypatch.setattr(fake_bot, "send_message", telegram_down)
    await scheduler.tick(fake_bot, db, EVENING, OWNER)
    await scheduler.tick(fake_bot, db, EVENING + timedelta(seconds=30), OWNER)
    # Не собираем заново каждый тик (лимит GitHub), и неушедшее не считается присланным.
    assert len(fetches) == 1 and await news_repo.sent_keys(db) == set()
    assert (await settings.get(db, OWNER))["news_next_at"] == "2026-10-17 15:10:00"

    del fake_bot.send_message  # связь вернулась
    await scheduler.tick(fake_bot, db, EVENING + timedelta(minutes=10), OWNER)
    assert "first.dev" in fake_bot.sent[-1][1] and len(fetches) == 2
    assert (await settings.get(db, OWNER))["news_next_at"] == "2026-10-18 15:00:00"


async def test_news_right_after_update_is_only_scheduled(db, user, fake_bot):
    # Сразу после миграции news_next_at = NULL: в сеть не ходим (offline_news упал бы), только планируем.
    await settings.update(db, OWNER, news_on=1, news_next_at=None)
    await scheduler.tick(fake_bot, db, NOW, OWNER)
    assert fake_bot.sent == []
    assert (await settings.get(db, OWNER))["news_next_at"] == "2026-10-17 15:00:00"


async def test_news_sent_from_settings_is_not_repeated_in_the_evening(db, harness, fake_bot, monkeypatch):
    async def fetched(now):
        return {"top": _items("top", "now"), "show": _items("show", "later")}

    monkeypatch.setattr(news, "fetch_candidates", fetched)
    await harness.click(SetCb(act="menu"))
    assert "📰 IT-новости: 20:00" in harness.text  # включены сразу
    await harness.click(SetCb(act="sched", v="news"))
    await harness.click(harness.button("Прислать сейчас"))
    assert harness.toasts[-1] == "Собираю новости…"
    assert 'href="https://now.dev"' in harness.text and 'href="https://later.dev"' in harness.text
    assert harness.screens()[-1].link_preview_options.is_disabled
    assert await news_repo.sent_keys(db) == {"now.dev", "later.dev"}

    await settings.update(db, OWNER, news_next_at="2026-01-01 00:00:00")  # пора вечерней подборки
    await scheduler.tick(fake_bot, db, tu.utcnow(), OWNER)
    assert [text for _, text, _ in fake_bot.sent] == [news_views.EMPTY]


async def test_double_press_sends_two_different_batches(db, harness, monkeypatch):
    monkeypatch.setattr(news, "LOCK", asyncio.Lock())  # замок к циклу событий этого теста

    async def slow(now):
        await asyncio.sleep(0.05)
        return {"top": _items("top", *(f"n{i}" for i in range(2 * news.LIMIT)))}

    monkeypatch.setattr(news, "fetch_candidates", slow)
    await asyncio.gather(harness.click(SetCb(act="newsnow")), harness.click(SetCb(act="newsnow")))
    first, second = [set(re.findall(r'href="([^"]+)"', m.text)) for m in harness.calls(SendMessage)]
    assert len(first) == len(second) == news.LIMIT and not first & second


async def test_migration_turns_on_evening_news():
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    try:
        for number, script in enumerate(database.MIGRATIONS[:14], start=1):
            await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
        await conn.execute("INSERT INTO settings (user_id, tz) VALUES (?, ?)", (OWNER, TZ))
        await database.migrate(conn)
        row = await database.fetchone(conn, "SELECT news_on, news_time, news_next_at FROM settings")
        assert tuple(row) == (1, "20:00", None)
        assert await database.scalar(conn, "SELECT COUNT(*) FROM news_sent") == 0
    finally:
        await conn.close()
