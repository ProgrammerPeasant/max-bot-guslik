"""Пульт розыгрышей: опечатать, разыграть, подтвердить, выгрузить, проверить.

Жизненный цикл розыгрыша:
    sealed     ключ сгенерирован, отпечаток можно публиковать
    drawn      победители определены, ждем администратора
    confirmed  администратор проверил результат
    announced  уведомления разосланы
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from festpass import lots
from festpass.clock import Clock
from festpass.config import Lineup
from festpass.store import Store

FINAL = "final"


class DrawError(Exception):
    pass


class Desk:
    def __init__(self, store: Store, lineup: Lineup, clock: Clock):
        self.store = store
        self.lineup = lineup
        self.clock = clock

    def seal_all(self) -> list:
        """Опечатать все дни и финал. Повторный вызов ничего не меняет."""
        for act in self.lineup.acts:
            self._seal("daily", act.key)
        self._seal(FINAL, FINAL)
        return self.store.draws()

    def _seal(self, kind: str, key: str):
        found = self.store.find_draw(kind, key)
        if found:
            return found
        seed = lots.new_seed()
        return self.store.create_draw(kind, key, seed, lots.fingerprint(seed), self.clock.stamp())

    def _fresh(self, kind: str, key: str):
        draw = self._seal(kind, key)
        if draw["status"] != "sealed":
            raise DrawError(f"розыгрыш #{draw['id']} уже проведен, статус {draw['status']}")
        return draw

    def get(self, draw_id: int):
        draw = self.store.draw(draw_id)
        if draw is None:
            raise DrawError(f"розыгрыша #{draw_id} нет")
        return draw

    def title(self, draw) -> str:
        if draw["kind"] == FINAL:
            return "Финал"
        act = self.lineup.act_on(date.fromisoformat(draw["key"]))
        return f"{draw['key']} {act.partner if act else ''}".strip()

    # проведение

    def draw_day(self, day: date) -> int:
        act = self.lineup.act_on(day)
        if act is None:
            raise DrawError(f"{day} нет в афише")
        if day > self.clock.today():
            raise DrawError("этот день еще не наступил")
        draw = self._fresh("daily", act.key)
        weights = {user_id: 1 for user_id in self.store.entrants(act.key)}
        order = lots.draw(draw["seed"], weights, act.winners)
        places = [
            (n, user_id, "winner", act.prize_at(n), "pending")
            for n, user_id in enumerate(order, 1)
        ]
        self.store.save_result(draw["id"], weights, places, self.clock.stamp())
        return draw["id"]

    def draw_final(self) -> int:
        if self.clock.today() < self.lineup.closing:
            raise DrawError("финал разыгрывается не раньше последнего дня фестиваля")
        draw = self._fresh(FINAL, FINAL)
        final = self.lineup.final
        weights = self.store.chance_weights(self.lineup.max_chances)
        order = lots.draw(draw["seed"], weights, final.winners + final.reserves)
        places = []
        for n, user_id in enumerate(order, 1):
            if n <= final.winners:
                places.append((n, user_id, "winner", final.prize, "pending"))
            else:
                places.append((n, user_id, "reserve", final.prize, "waiting"))
        self.store.save_result(draw["id"], weights, places, self.clock.stamp())
        return draw["id"]

    def confirm(self, draw_id: int, by: str) -> None:
        draw = self.get(draw_id)
        if draw["status"] != "drawn":
            raise DrawError(f"подтвердить можно только проведенный розыгрыш, сейчас {draw['status']}")
        self.store.update_draw(draw_id, status="confirmed", confirmed_at=self.clock.stamp(), confirmed_by=by)

    # проверка и отчетность

    def verify(self, draw_id: int) -> tuple[bool, str]:
        draw = self.get(draw_id)
        if draw["status"] == "sealed":
            raise DrawError("розыгрыш еще не проведен")
        if lots.fingerprint(draw["seed"]) != draw["seed_hash"]:
            return False, "ключ не совпадает с опубликованным отпечатком"
        places = self.store.places(draw_id)
        expected = lots.draw(draw["seed"], self.store.entrant_weights(draw_id), len(places))
        if expected != [p["user_id"] for p in places]:
            return False, "пересчет дал другой список мест"
        return True, f"сошлось: {len(places)} мест, участников {len(self.store.entrant_weights(draw_id))}"

    def export(self, draw_id: int, folder: Path) -> Path:
        """CSV для команды MAX и партнера. Разделитель ; и BOM, чтобы Excel открыл без танцев."""
        draw = self.get(draw_id)
        if draw["status"] == "sealed":
            raise DrawError("выгружать нечего, розыгрыш еще не проведен")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{draw['kind']}-{draw['key']}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            out = csv.writer(fh, delimiter=";")
            out.writerow(["Розыгрыш", self.title(draw)])
            out.writerow(["Проведен", draw["drawn_at"]])
            out.writerow(["Подтвержден", draw["confirmed_at"] or "нет", draw["confirmed_by"] or ""])
            out.writerow(["Отпечаток ключа", draw["seed_hash"]])
            out.writerow(["Ключ", draw["seed"]])
            out.writerow([])
            out.writerow([
                "user_id", "Имя в MAX", "username", "Билетов", "Место", "Роль",
                "Приз", "Статус", "Имя и фамилия", "Согласие на передачу",
            ])
            for row in self.store.export_rows(draw_id):
                consent = {1: "да", 0: "нет"}.get(row["consent"], "")
                out.writerow([
                    row["user_id"], row["name"] or "", row["username"] or "", row["weight"],
                    row["place"] or "", row["role"] or "", row["prize"] or "",
                    row["status"] or "", row["full_name"] or "", consent,
                ])
        return path
