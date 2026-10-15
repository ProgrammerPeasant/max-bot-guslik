from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from festpass.app import assemble
from festpass.maxapi import ApiError

ROOT = Path(__file__).resolve().parent.parent

LINEUP = """
[festival]
timezone = "Europe/Moscow"
max_chances = 3
rules_url = "https://example.com/rules"

[final]
prize = "два билета на концерт"
winners = 2
reserves = 2
claim_hours = 24
data_recipient = "билетному оператору"

[[act]]
day = 2026-09-01
partner = "Зерно"
channel_id = -1
channel_url = "https://max.ru/zerno"
promo_where = "кассе"
promo_until = 2026-09-30
instructions = "покажи сообщение на кассе"
prizes = [{ name = "кружка", count = 1 }, { name = "стикеры", count = 2 }]

[[act]]
day = 2026-09-02
partner = "Вжух"
channel_id = -2
channel_url = "https://max.ru/vzhuh"
promo_where = "приложении"
promo_until = 2026-10-01
instructions = "жди письма"
prizes = [{ name = "месяц поездок", count = 1 }]

[[act]]
day = 2026-09-04
partner = "Книжная лавка"
channel_id = -4
channel_url = "https://max.ru/books"
promo_where = "сайте"
promo_until = 2026-10-04
instructions = "приходи в магазин"
prizes = [{ name = "книга", count = 2 }]
"""


class FakeApi:
    """Запоминает все, что бот пытался отправить."""

    def __init__(self):
        self.outbox = []
        self.members: set[tuple[int, int]] = set()
        self.gone: set[int] = set()
        self.broken = False

    async def send(self, user_id, reply):
        if user_id in self.gone:
            raise ApiError(403, "chat.denied")
        self.outbox.append((user_id, reply))

    async def answer(self, callback_id, message=None, notification=None):
        uid = int(callback_id.split("-")[0])
        if message is not None:
            self.outbox.append((uid, message))

    async def is_member(self, chat_id, user_id):
        if self.broken:
            raise ApiError(500, "oops")
        return (chat_id, user_id) in self.members

    def last(self, user_id):
        for uid, reply in reversed(self.outbox):
            if uid == user_id:
                return reply
        return None


class Festival:
    """Удобная обертка для сценариев: festival.press(uid, "promo")."""

    def __init__(self, stage, api):
        self.stage = stage
        self.api = api
        self.bot = stage.bot(api)
        self.herald = stage.herald(api, rate=0)
        self._clicks = 0

    def at(self, moment: str):
        self.stage.clock.fixed = datetime.fromisoformat(moment).replace(tzinfo=self.stage.clock.tz)

    async def press(self, user_id: int, payload: str):
        self._clicks += 1
        await self.bot.handle({
            "update_type": "message_callback",
            "callback": {
                "callback_id": f"{user_id}-{self._clicks}",
                "payload": payload,
                "user": {"user_id": user_id, "name": f"user{user_id}"},
            },
        })
        return self.api.last(user_id)

    async def say(self, user_id: int, text: str):
        await self.bot.handle({
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": user_id, "name": f"user{user_id}"},
                "recipient": {"chat_type": "dialog"},
                "body": {"text": text},
            },
        })
        return self.api.last(user_id)

    async def visit(self, user_id: int, act_index: int):
        """Подписаться на партнера дня и пройти проверку."""
        act = self.stage.lineup.acts[act_index]
        self.api.members.add((act.channel_id, user_id))
        return await self.press(user_id, "check")


@pytest.fixture
def fest(tmp_path):
    lineup = tmp_path / "lineup.toml"
    lineup.write_text(LINEUP, encoding="utf-8")
    stage = assemble(str(lineup), str(ROOT / "texts.toml"), str(tmp_path / "test.db"))
    festival = Festival(stage, FakeApi())
    festival.at("2026-09-01 12:00")
    yield festival
    stage.store.close()
