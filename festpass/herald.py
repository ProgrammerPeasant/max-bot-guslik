"""Все исходящие сообщения по инициативе бота: победителям и рассылки."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from festpass.clock import Clock
from festpass.config import Lineup
from festpass.desk import FINAL, DrawError
from festpass.maxapi import ApiError, Reply
from festpass.screens import Screens
from festpass.store import Store

log = logging.getLogger(__name__)


class Herald:
    def __init__(self, api, store: Store, lineup: Lineup, screens: Screens, clock: Clock, rate: float = 20.0):
        self.api = api
        self.store = store
        self.lineup = lineup
        self.screens = screens
        self.clock = clock
        self.pause = 1.0 / rate if rate > 0 else 0.0

    async def _deliver(self, user_id: int, reply: Reply) -> bool:
        try:
            await self.api.send(user_id, reply)
            return True
        except ApiError as exc:
            if exc.user_gone:
                self.store.mark_blocked(user_id)
            log.warning("сообщение для %s не ушло: %s", user_id, exc)
            return False
        finally:
            if self.pause:
                await asyncio.sleep(self.pause)

    async def announce(self, draw_id: int) -> int:
        """Разослать уведомления победителям. Только после подтверждения админом."""
        draw = self.store.draw(draw_id)
        if draw is None:
            raise DrawError(f"розыгрыша #{draw_id} нет")
        if draw["status"] not in ("confirmed", "announced"):
            raise DrawError("результаты сначала должен подтвердить администратор: festbot confirm")

        now = self.clock.now()
        act = None if draw["kind"] == FINAL else self.lineup.act_on(date.fromisoformat(draw["key"]))
        sent = 0
        for place in self.store.places(draw_id, role="winner", status="pending"):
            if act is None:
                deadline = now + timedelta(hours=self.lineup.final.claim_hours)
                reply = self.screens.final_win(draw_id, deadline)
            else:
                deadline = None
                reply = self.screens.daily_win(act, place["prize"])
            ok = await self._deliver(place["user_id"], reply)
            sent += ok
            self.store.update_place(
                draw_id,
                place["place"],
                status="notified" if ok else "unreachable",
                notified_at=now.isoformat(),
                deadline=deadline.isoformat() if deadline else None,
            )
        if draw["status"] == "confirmed":
            self.store.update_draw(draw_id, status="announced", announced_at=now.isoformat())
        return sent

    async def sweep(self) -> int:
        """Передать призы молчунов резервным победителям. Возвращает число замен."""
        moved = 0
        touched = set()
        for place in self.store.overdue(self.clock.stamp()):
            self.store.update_place(place["draw_id"], place["place"], status="expired")
            if place["status"] == "notified":
                await self._deliver(place["user_id"], self.screens.claim_expired())
            reserve = self.store.next_reserve(place["draw_id"])
            if reserve is None:
                log.warning("в розыгрыше #%s закончились резервные победители", place["draw_id"])
                continue
            self.store.update_place(place["draw_id"], reserve["place"], role="winner", status="pending")
            touched.add(place["draw_id"])
            moved += 1
        for draw_id in touched:
            await self.announce(draw_id)
        return moved

    async def push(self, text: str, segment: str) -> tuple[int, int]:
        audience = self.store.audience(segment, self.clock.today().isoformat())
        reply = self.screens.broadcast(text)
        sent = 0
        for user_id in audience:
            sent += await self._deliver(user_id, reply)
        failed = len(audience) - sent
        self.store.log_push(text, segment, sent, failed, self.clock.stamp())
        return sent, failed
