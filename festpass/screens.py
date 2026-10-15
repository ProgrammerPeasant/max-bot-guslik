"""Экраны бота: текст из texts.toml плюс кнопки.

Здесь нет логики фестиваля, только раскладка. Payload кнопок короткий
и читаемый: "promo", "check", "claim:12", "agree:12".
"""

from __future__ import annotations

from datetime import date, datetime

from festpass.config import Act, Lineup, Texts, human_date
from festpass.maxapi import Button, Reply


def when(moment: datetime) -> str:
    return f"{human_date(moment.date())}, {moment:%H:%M}"


class Screens:
    def __init__(self, texts: Texts, lineup: Lineup):
        self.t = texts
        self.lineup = lineup

    def _button(self, name: str, /, payload: str | None = None, url: str | None = None, **values) -> Button:
        return Button(self.t.button(name, **values), payload=payload, url=url)

    def _to_menu(self) -> list[Button]:
        return [self._button("menu", "menu")]

    # меню и справка

    def welcome(self, banner: bool = False) -> Reply:
        rows = [
            [self._button("promo", "promo")],
            [self._button("join", "join")],
            [self._button("how", "how")],
            [self._button("chances", "pass")],
            [self._button("rules", "rules")],
        ]
        image = self.lineup.banner if banner else None
        return Reply(self.t.screen("welcome"), rows, image=image)

    def how(self) -> Reply:
        text = self.t.screen("how", prize=self.lineup.final.prize, max=self.lineup.max_chances)
        return Reply(text, [[self._button("join", "join")], self._to_menu()])

    def rules(self, seal: str | None) -> Reply:
        seal_text = self.t.screen("seal_today", hash=seal) if seal else self.t.screen("seal_none")
        text = self.t.screen(
            "rules",
            prize=self.lineup.final.prize,
            max=self.lineup.max_chances,
            seal=seal_text,
            rules_url=self.lineup.rules_url,
        )
        return Reply(text, [self._to_menu()])

    def idle(self, today: date) -> Reply:
        """Сегодня партнера нет: фестиваль еще не начался, антракт или финиш."""
        upcoming = self.lineup.next_after(today)
        if today < self.lineup.opening:
            text = self.t.screen("before", date=human_date(self.lineup.opening))
        elif upcoming:
            text = self.t.screen("pause", date=human_date(upcoming.day))
        else:
            text = self.t.screen("after")
        return Reply(text, [self._to_menu()])

    # промокод

    def promo_code(self, act: Act, code: str) -> Reply:
        text = self.t.screen(
            "promo_code",
            partner=act.partner,
            code=code,
            until=human_date(act.promo_until),
            where=act.promo_where,
        )
        rows = [[self._button("join", "join")], self._to_menu()]
        return Reply(text, rows, keep=True, toast=self.t.toast("promo"))

    def promo_sold_out(self, act: Act) -> Reply:
        text = self.t.screen("promo_sold_out", partner=act.partner)
        return Reply(text, [[self._button("join", "join")], self._to_menu()])

    # розыгрыш дня

    def join(self, act: Act) -> Reply:
        rows = [
            [self._button("subscribe", url=act.channel_url, partner=act.partner)],
            [self._button("check", "check")],
            self._to_menu(),
        ]
        return Reply(self.t.screen("join", partner=act.partner), rows)

    def not_subscribed(self, act: Act) -> Reply:
        rows = [
            [self._button("subscribe_short", url=act.channel_url)],
            [self._button("check_again", "check")],
            self._to_menu(),
        ]
        return Reply(self.t.screen("not_subscribed", partner=act.partner), rows)

    def registered(self, act: Act, chances: int) -> Reply:
        upcoming = self.lineup.next_after(act.day)
        if upcoming is None:
            tail = self.t.screen("next_none")
        elif (upcoming.day - act.day).days == 1:
            tail = self.t.screen("next_tomorrow")
        else:
            tail = self.t.screen("next_later", date=human_date(upcoming.day))
        text = self.t.screen(
            "registered",
            partner=act.partner,
            chances=chances,
            max=self.lineup.max_chances,
            next=tail,
        )
        rows = [[self._button("chances", "pass")], self._to_menu()]
        return Reply(text, rows, keep=True, toast=self.t.toast("registered"))

    def already_in(self) -> Reply:
        return Reply(self.t.screen("already_in"), [[self._button("chances", "pass")], self._to_menu()])

    def check_failed(self) -> Reply:
        return Reply(self.t.screen("check_failed"), [[self._button("check_again", "check")], self._to_menu()])

    def registration_closed(self, act: Act) -> Reply:
        return Reply(self.t.screen("registration_closed", partner=act.partner), [self._to_menu()])

    # фестивальный пропуск

    def festival_pass(self, today: date, visited: set[str], chances: int) -> Reply:
        cap = self.lineup.max_chances
        strip = self.t.mark("slot_on") * chances + self.t.mark("slot_off") * (cap - chances)
        rows = []
        for act in self.lineup.acts:
            if act.key in visited:
                mark = self.t.mark("visited")
            elif act.day == today:
                mark = self.t.mark("today")
            elif act.day < today:
                mark = self.t.mark("missed")
            else:
                mark = self.t.mark("ahead")
            line = self.t.screen("pass_row", date=f"{act.day:%d.%m}", partner=act.partner, mark=mark)
            rows.append(line.rstrip())
        text = self.t.screen(
            "pass",
            chances=chances,
            max=cap,
            strip=strip,
            lineup="\n".join(rows),
            left=self.lineup.days_left(today),
            prize=self.lineup.final.prize,
        )
        return Reply(text, [[self._button("join", "join")], self._to_menu()])

    # победителям

    def daily_win(self, act: Act, prize: str) -> Reply:
        text = self.t.screen(
            "daily_win", prize=prize, partner=act.partner, instructions=act.instructions
        )
        return Reply(text, [self._to_menu()], keep=True)

    def final_win(self, draw_id: int, deadline: datetime) -> Reply:
        text = self.t.screen("final_win", prize=self.lineup.final.prize, deadline=when(deadline))
        return Reply(text, [[self._button("claim", f"claim:{draw_id}")]], keep=True)

    def claim_name(self) -> Reply:
        return Reply(self.t.screen("claim_name"), keep=True, toast=self.t.toast("claim"))

    def claim_name_bad(self) -> Reply:
        return Reply(self.t.screen("claim_name_bad"), keep=True)

    def claim_consent(self, draw_id: int, name: str) -> Reply:
        text = self.t.screen("claim_consent", name=name, recipient=self.lineup.final.data_recipient)
        rows = [
            [self._button("agree", f"agree:{draw_id}")],
            [self._button("refuse", f"refuse:{draw_id}")],
        ]
        return Reply(text, rows, keep=True)

    def claim_done(self) -> Reply:
        return Reply(self.t.screen("claim_done"), [self._to_menu()], keep=True)

    def claim_declined(self, draw_id: int, deadline: datetime) -> Reply:
        text = self.t.screen("claim_declined", deadline=when(deadline))
        return Reply(text, [[self._button("claim", f"claim:{draw_id}")]], keep=True)

    def claim_expired(self) -> Reply:
        return Reply(self.t.screen("claim_expired"), [self._to_menu()], keep=True)

    def claim_unknown(self) -> Reply:
        return Reply(self.t.screen("claim_unknown"), [self._to_menu()])

    def broadcast(self, text: str) -> Reply:
        return Reply(text, [self._to_menu()], keep=True)
