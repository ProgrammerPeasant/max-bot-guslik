from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


class Clock:
    """Фестивальное время.

    Сутки считаются по часовому поясу фестиваля, а не сервера: иначе
    бот на машине в UTC в три часа ночи по Москве жил бы еще вчерашним
    партнером. В тестах время можно заморозить через fixed.
    """

    def __init__(self, tz: str, fixed: datetime | None = None):
        self.tz = ZoneInfo(tz)
        self.fixed = fixed

    def now(self) -> datetime:
        if self.fixed is not None:
            return self.fixed
        return datetime.now(self.tz).replace(microsecond=0)

    def today(self) -> date:
        return self.now().date()

    def stamp(self) -> str:
        return self.now().isoformat()
