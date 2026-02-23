from __future__ import annotations

import importlib.util
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from telegram import Message, Update
    from telegram.ext import ContextTypes


DB_PATH = Path("respect.db")
TOKEN_PATH = Path("token.txt")
MESSAGES_PATH = Path("messages.txt")


DEFAULT_MESSAGES = {
    "all_empty": "В группе пока некому отправить упоминание.",
    "all_text": "🔔 Призыв всем участникам:\n{mentions}",
    "bonus_claimed": "🎁 {user}, вы забрали ежедневный бонус уважения!",
    "bonus_cooldown": "⏳ {user}, бонус уже был получен сегодня. Попробуйте позже.",
    "respect_given": "✅ Уважение оказано: {target}\n{scale}",
    "respect_taken": "➖ Уважение уменьшено: {target}\n{scale}",
    "respect_self": "Нельзя изменять уважение самому себе.",
    "reply_required": "Эту команду нужно отправлять ответом на сообщение пользователя.",
    "level_up": "🏆 {user} достиг {level} уровня уважения!",
}


@dataclass
class UserRespect:
    user_id: int
    username: str
    respect: int
    level: int


class RespectStorage:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    respect INTEGER NOT NULL DEFAULT 1,
                    last_bonus_date TEXT,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )

    def ensure_user(self, chat_id: int, user_id: int, username: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO users (chat_id, user_id, username, respect)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET username=excluded.username
                """,
                (chat_id, user_id, username),
            )

    def get_user(self, chat_id: int, user_id: int) -> UserRespect:
        row = self.conn.execute(
            "SELECT user_id, username, respect FROM users WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("User not found")
        level = calculate_level(row["respect"])
        return UserRespect(row["user_id"], row["username"], row["respect"], level)

    def update_respect(self, chat_id: int, user_id: int, delta: int) -> UserRespect:
        with self.conn:
            self.conn.execute(
                "UPDATE users SET respect = MAX(1, respect + ?) WHERE chat_id=? AND user_id=?",
                (delta, chat_id, user_id),
            )
        return self.get_user(chat_id, user_id)

    def claim_bonus(self, chat_id: int, user_id: int, date_str: str) -> tuple[bool, UserRespect]:
        row = self.conn.execute(
            "SELECT last_bonus_date FROM users WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("User not found")

        if row["last_bonus_date"] == date_str:
            return False, self.get_user(chat_id, user_id)

        with self.conn:
            self.conn.execute(
                "UPDATE users SET respect = respect + 1, last_bonus_date=? WHERE chat_id=? AND user_id=?",
                (date_str, chat_id, user_id),
            )
        return True, self.get_user(chat_id, user_id)

    def list_mentions(self, chat_id: int, except_user_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT user_id, username FROM users WHERE chat_id=? AND user_id!=? ORDER BY username",
            (chat_id, except_user_id),
        ).fetchall()
        return [f"[{r['username']}](tg://user?id={r['user_id']})" for r in rows]


def calculate_level(respect: int) -> int:
    return ((respect - 1) // 10) + 1


def build_scale(respect: int) -> str:
    level = calculate_level(respect)
    points_in_level = (respect - 1) % 10
    filled = "█" * points_in_level
    empty = "░" * (10 - points_in_level)
    return f"`{respect}`\n`[{filled}{empty}]`\nУровень: *{level}*"


def load_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"Файл {path} пуст. Укажите токен бота.")
    return token


def load_messages(path: Path) -> Dict[str, str]:
    if not path.exists():
        save_messages(path, DEFAULT_MESSAGES)
        return DEFAULT_MESSAGES.copy()

    messages: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        messages[key.strip()] = value.strip().replace("\\n", "\n")

    merged = DEFAULT_MESSAGES.copy()
    merged.update(messages)
    return merged


def save_messages(path: Path, data: Dict[str, str]) -> None:
    lines = ["# Редактируйте фразы бота в формате ключ=значение."]
    for key, value in data.items():
        escaped = value.replace('\n', r'\\n')
        lines.append(f"{key}={escaped}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_username(message: Message) -> str:
    user = message.from_user
    if user.username:
        return f"@{user.username}"
    full_name = (user.full_name or "").strip()
    return full_name if full_name else str(user.id)


async def handle_message(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    message = update.effective_message
    if message is None or message.from_user is None or message.chat is None:
        return

    if message.chat.type not in {"group", "supergroup"}:
        return

    storage: RespectStorage = context.application.bot_data["storage"]
    phrases: Dict[str, str] = context.application.bot_data["messages"]

    author = extract_username(message)
    storage.ensure_user(message.chat.id, message.from_user.id, author)

    text = (message.text or "").strip()
    if not text:
        return

    if text == ".all":
        mentions = storage.list_mentions(message.chat.id, message.from_user.id)
        if not mentions:
            await message.reply_text(phrases["all_empty"])
            return
        await message.reply_text(
            phrases["all_text"].format(mentions=" ".join(mentions)),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    if text == ".bonus":
        today = datetime.now(timezone.utc).date().isoformat()
        claimed, user_info = storage.claim_bonus(message.chat.id, message.from_user.id, today)
        scale = build_scale(user_info.respect)
        if claimed:
            response = phrases["bonus_claimed"].format(user=author)
            await message.reply_text(f"{response}\n{scale}", parse_mode="Markdown")
            prev_level = calculate_level(user_info.respect - 1)
            if user_info.level > prev_level:
                await message.reply_text(
                    phrases["level_up"].format(user=author, level=user_info.level)
                )
        else:
            response = phrases["bonus_cooldown"].format(user=author)
            await message.reply_text(f"{response}\n{scale}", parse_mode="Markdown")
        return

    if text in {"+", "-"}:
        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.reply_text(phrases["reply_required"])
            return

        target_message = message.reply_to_message
        target_author = extract_username(target_message)
        target_user = target_message.from_user

        storage.ensure_user(message.chat.id, target_user.id, target_author)

        if target_user.id == message.from_user.id:
            await message.reply_text(phrases["respect_self"])
            return

        delta = 1 if text == "+" else -1
        prev = storage.get_user(message.chat.id, target_user.id)
        updated = storage.update_respect(message.chat.id, target_user.id, delta)

        scale = build_scale(updated.respect)
        template_key = "respect_given" if delta > 0 else "respect_taken"
        await message.reply_text(
            phrases[template_key].format(target=target_author, scale=scale),
            parse_mode="Markdown",
        )

        if updated.level > prev.level:
            await message.reply_text(
                phrases["level_up"].format(user=target_author, level=updated.level)
            )


def ensure_config_files() -> None:
    if not MESSAGES_PATH.exists():
        save_messages(MESSAGES_PATH, DEFAULT_MESSAGES)

    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text("PASTE_TELEGRAM_BOT_TOKEN_HERE\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_config_files()

    if importlib.util.find_spec("telegram") is None:
        logging.error("Не найден пакет 'python-telegram-bot'. Установите зависимости: pip install -r requirements.txt")
        return

    from telegram import Update
    from telegram.ext import Application, MessageHandler, filters

    token = load_token(TOKEN_PATH)
    if token == "PASTE_TELEGRAM_BOT_TOKEN_HERE":
        logging.error("Укажите токен в token.txt перед запуском.")
        return

    messages = load_messages(MESSAGES_PATH)
    storage = RespectStorage(DB_PATH)

    app = Application.builder().token(token).build()
    app.bot_data["messages"] = messages
    app.bot_data["storage"] = storage

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
