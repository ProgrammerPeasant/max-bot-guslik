"""Сборка зависимостей и основной цикл long polling."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

from festpass.bot import FestBot
from festpass.clock import Clock
from festpass.config import Lineup, Texts, load_lineup
from festpass.desk import Desk
from festpass.herald import Herald
from festpass.maxapi import ApiError, MaxApi
from festpass.screens import Screens
from festpass.store import Store

log = logging.getLogger("festpass")

SWEEP_EVERY = 300


@dataclass
class Stage:
    lineup: Lineup
    texts: Texts
    store: Store
    clock: Clock
    screens: Screens
    desk: Desk

    def herald(self, api, rate: float = 20.0) -> Herald:
        return Herald(api, self.store, self.lineup, self.screens, self.clock, rate)

    def bot(self, api) -> FestBot:
        return FestBot(api, self.store, self.lineup, self.screens, self.clock)


def assemble(lineup_path: str, texts_path: str, db_path: str) -> Stage:
    lineup = load_lineup(lineup_path)
    texts = Texts.load(texts_path)
    store = Store(db_path)
    clock = Clock(lineup.timezone)
    return Stage(lineup, texts, store, clock, Screens(texts, lineup), Desk(store, lineup, clock))


def connect() -> MaxApi:
    token = os.environ.get("MAX_BOT_TOKEN")
    if not token:
        raise SystemExit("не задан MAX_BOT_TOKEN")
    return MaxApi(token)


async def _sweep_forever(herald: Herald) -> None:
    while True:
        await asyncio.sleep(SWEEP_EVERY)
        try:
            moved = await herald.sweep()
            if moved:
                log.info("призы переданы резервным победителям: %s", moved)
        except Exception:
            log.exception("обход просроченных призов упал, попробуем позже")


async def serve(stage: Stage) -> None:
    api = connect()
    bot = stage.bot(api)
    me = await api.me()
    log.info("бот %s на связи, афиша: %s дней", me.get("name") or me.get("username"), len(stage.lineup.acts))

    sweeper = asyncio.create_task(_sweep_forever(stage.herald(api)))
    marker, backoff = None, 1
    try:
        while True:
            try:
                batch = await api.updates(marker)
            except (httpx.HTTPError, ApiError) as exc:
                log.warning("long polling: %s, повтор через %s с", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            backoff = 1
            marker = batch.get("marker", marker)
            for update in batch.get("updates", []):
                try:
                    await bot.handle(update)
                except Exception:
                    log.exception("апдейт %s не обработан", update.get("update_type"))
    finally:
        sweeper.cancel()
        await api.close()
