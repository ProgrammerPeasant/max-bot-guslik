"""Диалог с участником: разбор апдейтов MAX и сценарии из ТЗ."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from festpass.clock import Clock
from festpass.config import Lineup
from festpass.maxapi import ApiError, Reply
from festpass.screens import Screens
from festpass.store import Store

log = logging.getLogger(__name__)

# состояние "ждем имя и фамилию для приза из розыгрыша N"
AWAIT_NAME = "name"
START_WORDS = {"/start", "start", "начать", "старт", "меню", "/menu"}
FULL_NAME = re.compile(r"[A-Za-zА-Яа-яЁё'-]{2,40}(\s+[A-Za-zА-Яа-яЁё'-]{2,40}){1,2}")


class FestBot:
    def __init__(self, api, store: Store, lineup: Lineup, screens: Screens, clock: Clock):
        self.api = api
        self.store = store
        self.lineup = lineup
        self.screens = screens
        self.clock = clock
        self.actions = {
            "start": self.start,
            "menu": self.menu,
            "promo": self.promo,
            "join": self.join,
            "check": self.check,
            "how": self.how,
            "pass": self.festival_pass,
            "rules": self.rules,
            "claim": self.claim,
            "agree": self.agree,
            "refuse": self.refuse,
        }

    # входящие апдейты

    async def handle(self, update: dict) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            await self._route(update.get("user") or {}, "start", None)
        elif kind == "message_callback":
            callback = update.get("callback") or {}
            payload = callback.get("payload") or "menu"
            await self._route(callback.get("user") or {}, payload, callback.get("callback_id"))
        elif kind == "message_created":
            message = update.get("message") or {}
            chat_type = (message.get("recipient") or {}).get("chat_type", "dialog")
            sender = message.get("sender") or {}
            if chat_type != "dialog" or sender.get("is_bot"):
                return
            text = ((message.get("body") or {}).get("text") or "").strip()
            await self._on_text(sender, text)

    def _remember(self, user: dict) -> int | None:
        user_id = user.get("user_id")
        if not user_id:
            return None
        name = user.get("name") or user.get("first_name")
        self.store.touch_user(user_id, name, user.get("username"), self.clock.stamp())
        return user_id

    async def _route(self, user: dict, payload: str, callback_id: str | None) -> None:
        user_id = self._remember(user)
        if user_id is None:
            return
        action, _, arg = payload.partition(":")
        handler = self.actions.get(action, self.menu)
        reply = await handler(user_id, arg)
        await self._respond(user_id, reply, callback_id)

    async def _on_text(self, sender: dict, text: str) -> None:
        user_id = self._remember(sender)
        if user_id is None:
            return
        state = self.store.state_of(user_id) or ""
        if state.startswith(AWAIT_NAME + ":") and text.lower() not in START_WORDS:
            reply = self._take_name(user_id, int(state.split(":")[1]), text)
        else:
            self.store.set_state(user_id, None)
            reply = self.screens.welcome(banner=text.lower() in START_WORDS)
        await self._respond(user_id, reply, None)

    async def _respond(self, user_id: int, reply: Reply, callback_id: str | None) -> None:
        try:
            if callback_id and not reply.keep:
                await self.api.answer(callback_id, message=reply)
                return
            if callback_id:
                await self.api.answer(callback_id, notification=reply.toast)
            await self.api.send(user_id, reply)
        except ApiError as exc:
            if not exc.user_gone:
                raise
            self.store.mark_blocked(user_id)

    # главное меню

    async def start(self, user_id: int, arg: str) -> Reply:
        self.store.set_state(user_id, None)
        return self.screens.welcome(banner=True)

    async def menu(self, user_id: int, arg: str) -> Reply:
        self.store.set_state(user_id, None)
        return self.screens.welcome()

    async def how(self, user_id: int, arg: str) -> Reply:
        return self.screens.how()

    async def rules(self, user_id: int, arg: str) -> Reply:
        act = self.lineup.act_on(self.clock.today())
        draw = self.store.find_draw("daily", act.key) if act else None
        return self.screens.rules(draw["seed_hash"] if draw else None)

    async def festival_pass(self, user_id: int, arg: str) -> Reply:
        visited = self.store.active_days(user_id)
        chances = min(len(visited), self.lineup.max_chances)
        return self.screens.festival_pass(self.clock.today(), visited, chances)

    # промокод дня

    async def promo(self, user_id: int, arg: str) -> Reply:
        today, now = self.clock.today(), self.clock.stamp()
        act = self.lineup.act_on(today)
        if act is None:
            return self.screens.idle(today)
        self.store.log_promo_request(user_id, act.key, now)
        code = self.store.take_code(user_id, act.key, now)
        if code is None:
            return self.screens.promo_sold_out(act)
        self.store.mark_active(user_id, act.key, "promo", now)
        return self.screens.promo_code(act, code)

    # розыгрыш дня

    async def join(self, user_id: int, arg: str) -> Reply:
        today = self.clock.today()
        act = self.lineup.act_on(today)
        if act is None:
            return self.screens.idle(today)
        if self._closed(act.key):
            return self.screens.registration_closed(act)
        if self.store.entry_of(act.key, user_id):
            return self.screens.already_in()
        return self.screens.join(act)

    async def check(self, user_id: int, arg: str) -> Reply:
        today, now = self.clock.today(), self.clock.stamp()
        act = self.lineup.act_on(today)
        if act is None:
            return self.screens.idle(today)
        if self._closed(act.key):
            return self.screens.registration_closed(act)
        if self.store.entry_of(act.key, user_id):
            return self.screens.already_in()
        try:
            subscribed = await self.api.is_member(act.channel_id, user_id)
        except ApiError as exc:
            log.error("не проверили подписку на %s: %s", act.partner, exc)
            return self.screens.check_failed()
        if not subscribed:
            return self.screens.not_subscribed(act)
        if not self.store.register(act.key, user_id, now):
            return self.screens.already_in()
        self.store.mark_active(user_id, act.key, "draw", now)
        return self.screens.registered(act, self.store.chances(user_id, self.lineup.max_chances))

    def _closed(self, day: str) -> bool:
        draw = self.store.find_draw("daily", day)
        return draw is not None and draw["status"] != "sealed"

    # подтверждение главного приза

    def _claimable(self, user_id: int, arg: str):
        """Место победителя, по которому еще можно оставить данные."""
        if not arg.isdigit():
            return None
        place = self.store.place_of(int(arg), user_id)
        if place is None or place["role"] != "winner" or place["deadline"] is None:
            return None
        return place

    def _expired(self, place) -> bool:
        if place["status"] == "expired":
            return True
        return datetime.fromisoformat(place["deadline"]) < self.clock.now()

    async def claim(self, user_id: int, arg: str) -> Reply:
        place = self._claimable(user_id, arg)
        if place is None:
            return self.screens.claim_unknown()
        if place["status"] == "claimed":
            return self.screens.claim_done()
        if self._expired(place):
            return self.screens.claim_expired()
        self.store.set_state(user_id, f"{AWAIT_NAME}:{place['draw_id']}")
        return self.screens.claim_name()

    def _take_name(self, user_id: int, draw_id: int, text: str) -> Reply:
        name = " ".join(text.split())
        if not FULL_NAME.fullmatch(name):
            return self.screens.claim_name_bad()
        self.store.save_claim_name(draw_id, user_id, name, self.clock.stamp())
        self.store.set_state(user_id, None)
        return self.screens.claim_consent(draw_id, name)

    async def agree(self, user_id: int, arg: str) -> Reply:
        place = self._claimable(user_id, arg)
        if place is None or self.store.claim(place["draw_id"], user_id) is None:
            return self.screens.claim_unknown()
        if place["status"] == "claimed":
            return self.screens.claim_done()
        if self._expired(place):
            return self.screens.claim_expired()
        self.store.save_consent(place["draw_id"], user_id, True, self.clock.stamp())
        self.store.update_place(place["draw_id"], place["place"], status="claimed")
        return self.screens.claim_done()

    async def refuse(self, user_id: int, arg: str) -> Reply:
        place = self._claimable(user_id, arg)
        if place is None or self.store.claim(place["draw_id"], user_id) is None:
            return self.screens.claim_unknown()
        if place["status"] == "claimed" or self._expired(place):
            return await self.claim(user_id, arg)
        self.store.save_consent(place["draw_id"], user_id, False, self.clock.stamp())
        deadline = datetime.fromisoformat(place["deadline"])
        return self.screens.claim_declined(place["draw_id"], deadline)
