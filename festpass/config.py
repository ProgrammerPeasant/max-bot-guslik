"""Афиша фестиваля (lineup.toml) и тексты бота (texts.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Prize:
    name: str
    count: int


@dataclass(frozen=True)
class Act:
    """Один день фестиваля и партнер, который в этот день на сцене."""

    day: date
    partner: str
    channel_id: int
    channel_url: str
    promo_where: str
    promo_until: date
    instructions: str
    prizes: tuple[Prize, ...]

    @property
    def key(self) -> str:
        return self.day.isoformat()

    @property
    def winners(self) -> int:
        return sum(p.count for p in self.prizes)

    def prize_at(self, place: int) -> str:
        """Приз для места place (с единицы): призы раздаются в порядке из афиши."""
        left = place
        for prize in self.prizes:
            if left <= prize.count:
                return prize.name
            left -= prize.count
        raise IndexError(f"у {self.partner} нет приза для {place} места")


@dataclass(frozen=True)
class Final:
    prize: str
    winners: int
    reserves: int
    claim_hours: int
    data_recipient: str


@dataclass(frozen=True)
class Lineup:
    title: str
    timezone: str
    max_chances: int
    banner: str | None
    rules_url: str
    final: Final
    acts: tuple[Act, ...]

    @property
    def opening(self) -> date:
        return self.acts[0].day

    @property
    def closing(self) -> date:
        return self.acts[-1].day

    def act_on(self, day: date) -> Act | None:
        for act in self.acts:
            if act.day == day:
                return act
        return None

    def next_after(self, day: date) -> Act | None:
        for act in self.acts:
            if act.day > day:
                return act
        return None

    def days_left(self, day: date) -> int:
        """Сколько дней фестиваля еще впереди, сегодняшний тоже считается."""
        return sum(1 for act in self.acts if act.day >= day)


def _act(raw: dict) -> Act:
    prizes = tuple(Prize(str(p["name"]), int(p["count"])) for p in raw["prizes"])
    if any(p.count < 1 for p in prizes):
        raise ConfigError(f"{raw['partner']}: количество призов должно быть положительным")
    return Act(
        day=raw["day"],
        partner=raw["partner"],
        channel_id=int(raw["channel_id"]),
        channel_url=raw["channel_url"],
        promo_where=raw["promo_where"],
        promo_until=raw["promo_until"],
        instructions=raw["instructions"],
        prizes=prizes,
    )


def load_lineup(path: str | Path) -> Lineup:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    try:
        fest, fin = raw["festival"], raw["final"]
        acts = tuple(sorted((_act(item) for item in raw["act"]), key=lambda a: a.day))
        final = Final(
            prize=fin["prize"],
            winners=int(fin["winners"]),
            reserves=int(fin.get("reserves", 0)),
            claim_hours=int(fin.get("claim_hours", 48)),
            data_recipient=fin.get("data_recipient", "партнеру"),
        )
        lineup = Lineup(
            title=fest.get("title", "Бот розыгрышей"),
            timezone=fest.get("timezone", "Europe/Moscow"),
            max_chances=int(fest.get("max_chances", 10)),
            banner=fest.get("banner") or None,
            rules_url=fest.get("rules_url", ""),
            final=final,
            acts=acts,
        )
    except KeyError as exc:
        raise ConfigError(f"в {path} не хватает поля {exc}") from None

    if not acts:
        raise ConfigError("в афише нет ни одного дня")
    days = [a.day for a in acts]
    if len(set(days)) != len(days):
        raise ConfigError("в один день на сцене может быть только один партнер")
    for act in acts:
        if act.promo_until < act.day:
            raise ConfigError(f"{act.partner}: промокод истекает раньше, чем выдается")
    if final.winners < 1:
        raise ConfigError("в финале должен быть хотя бы один победитель")
    return lineup


class Texts:
    """Обертка над texts.toml: t.screen("welcome"), t.button("subscribe", partner=...)."""

    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def load(cls, path: str | Path) -> Texts:
        with open(path, "rb") as fh:
            return cls(tomllib.load(fh))

    def _get(self, section: str, name: str, values: dict) -> str:
        try:
            template = self._data[section][name]
        except KeyError:
            raise ConfigError(f"в texts.toml нет [{section}] {name}") from None
        return template.format(**values) if values else template

    def screen(self, name: str, /, **values) -> str:
        return self._get("screens", name, values)

    def button(self, name: str, /, **values) -> str:
        return self._get("buttons", name, values)

    def mark(self, name: str) -> str:
        return self._get("marks", name, {})

    def toast(self, name: str) -> str:
        return self._get("toasts", name, {})
