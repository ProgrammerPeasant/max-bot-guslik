"""Тонкий клиент MAX Bot API (platform-api.max.ru).

Берем только то, что нужно фестивалю: long polling, отправку
сообщений, ответ на нажатие кнопки и проверку участника канала.
Остальной код про HTTP ничего не знает и работает с Reply и Button,
поэтому в тестах клиент подменяется простой заглушкой.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

API_URL = "https://platform-api.max.ru"
UPDATE_TYPES = "bot_started,message_created,message_callback"


@dataclass
class Button:
    text: str
    payload: str | None = None
    url: str | None = None

    def as_json(self) -> dict:
        if self.url:
            return {"type": "link", "text": self.text, "url": self.url}
        return {"type": "callback", "text": self.text, "payload": self.payload}


@dataclass
class Reply:
    """Экран бота.

    keep=False: экран навигационный, при нажатии кнопки им можно
    заменить старое сообщение, чтобы чат не превращался в ленту меню.
    keep=True: сообщение должно остаться в истории (промокод, заявка,
    приз), его отправляем новым, а на нажатие отвечаем коротким toast.
    """

    text: str
    rows: list[list[Button]] = field(default_factory=list)
    image: str | None = None
    keep: bool = False
    toast: str | None = None

    def body(self) -> dict:
        attachments = []
        if self.image:
            attachments.append({"type": "image", "payload": {"url": self.image}})
        if self.rows:
            buttons = [[b.as_json() for b in row] for row in self.rows]
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": buttons}})
        body: dict = {"text": self.text}
        if attachments:
            body["attachments"] = attachments
        return body


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"MAX API {status}: {detail[:300]}")
        self.status = status

    @property
    def user_gone(self) -> bool:
        """Человек заблокировал бота или удалил чат."""
        return self.status in (403, 404)


class MaxApi:
    def __init__(self, token: str, base_url: str = API_URL, client: httpx.AsyncClient | None = None):
        self._http = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": token},
            timeout=httpx.Timeout(10.0, read=40.0),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(3):
            resp = await self._http.request(method, path, **kwargs)
            if resp.status_code != 429:
                break
            # лимит MAX порядка 30 запросов в секунду; на рассылке бывает
            await asyncio.sleep(1 + attempt)
        if resp.is_error:
            raise ApiError(resp.status_code, resp.text)
        return resp.json() if resp.content else {}

    async def me(self) -> dict:
        return await self._call("GET", "/me")

    async def updates(self, marker: int | None = None, timeout: int = 30) -> dict:
        params: dict = {"timeout": timeout, "types": UPDATE_TYPES}
        if marker is not None:
            params["marker"] = marker
        return await self._call("GET", "/updates", params=params)

    async def send(self, user_id: int, reply: Reply) -> None:
        await self._call("POST", "/messages", params={"user_id": user_id}, json=reply.body())

    async def answer(self, callback_id: str, message: Reply | None = None, notification: str | None = None) -> None:
        body: dict = {}
        if message is not None:
            body["message"] = message.body()
        if notification:
            body["notification"] = notification
        await self._call("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        """Есть ли человек среди участников канала. Бот должен быть там админом."""
        data = await self._call("GET", f"/chats/{chat_id}/members", params={"user_ids": user_id})
        return any(m.get("user_id") == user_id for m in data.get("members", []))
