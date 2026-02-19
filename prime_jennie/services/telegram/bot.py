"""Telegram Bot — Bot API 통신 + 명령 파싱.

Telegram Bot API로 메시지 수신/발송, 명령 파싱.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram Bot API 클라이언트."""

    def __init__(self, token: str, allowed_chat_ids: str = ""):
        self._token = token
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._allowed_ids: set[str] = set()
        if allowed_chat_ids:
            self._allowed_ids = {cid.strip() for cid in allowed_chat_ids.split(",") if cid.strip()}
        self._last_update_id: int = 0

    def is_authorized(self, chat_id: str | int) -> bool:
        """허용된 chat_id인지 확인."""
        if not self._allowed_ids:
            return True  # 미설정 시 전체 허용
        return str(chat_id) in self._allowed_ids

    def get_updates(self, timeout: int = 30) -> list[dict]:
        """Telegram getUpdates (long-polling)."""
        try:
            resp = httpx.get(
                f"{self._base_url}/getUpdates",
                params={
                    "offset": self._last_update_id + 1,
                    "timeout": timeout,
                    "allowed_updates": '["message"]',
                },
                timeout=timeout + 5,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            if not data.get("ok"):
                return []

            updates = data.get("result", [])
            if updates:
                self._last_update_id = updates[-1]["update_id"]
            return updates

        except Exception as e:
            logger.debug("Telegram getUpdates error: %s", e)
            return []

    def send_message(self, chat_id: str | int, text: str) -> bool:
        """메시지 발송."""
        try:
            resp = httpx.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": str(chat_id),
                    "text": text[:4096],
                    "parse_mode": "Markdown",
                },
                timeout=15,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    @staticmethod
    def parse_command(text: str) -> tuple[str | None, str]:
        """메시지 텍스트에서 명령어와 인자 추출.

        Returns:
            (command, args) — 명령이 아니면 (None, "")
        """
        if not text or not text.startswith("/"):
            return None, ""

        parts = text.strip().split(maxsplit=1)
        command = parts[0].lower()
        # @botname 제거
        if "@" in command:
            command = command.split("@")[0]
        args = parts[1] if len(parts) > 1 else ""
        return command, args

    def get_pending_commands(self) -> list[dict]:
        """인증된 사용자의 명령만 반환.

        Returns:
            list of {"chat_id", "command", "args", "username"}
        """
        updates = self.get_updates(timeout=5)
        commands = []
        for update in updates:
            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")

            if not chat_id or not text:
                continue

            if not self.is_authorized(chat_id):
                self.send_message(chat_id, "권한이 없습니다.")
                continue

            command, args = self.parse_command(text)
            if command:
                username = msg.get("from", {}).get("username", "unknown")
                commands.append(
                    {
                        "chat_id": chat_id,
                        "command": command,
                        "args": args,
                        "username": username,
                    }
                )

        return commands
