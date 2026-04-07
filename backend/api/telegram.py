"""
Telegram API endpoints.
POST /api/telegram/test  — send a real test message via the given bot credentials
"""
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramTestRequest(BaseModel):
    bot_token: str
    chat_id: str


@router.post("/test")
async def test_telegram(req: TelegramTestRequest):
    """
    Send a test Telegram message using the provided credentials.
    Returns ok=True on success, ok=False with an error string on failure.
    """
    try:
        from telegram import Bot
        from telegram.error import TelegramError
    except ImportError:
        return {"ok": False, "error": "python-telegram-bot not installed on server"}

    try:
        bot = Bot(token=req.bot_token)
        me = await bot.get_me()
        await bot.send_message(
            chat_id=req.chat_id,
            text=(
                "✅ <b>Hypertrade alert system connected</b>\n\n"
                "This is a test message from your Hypertrade dashboard.\n"
                "You will receive signal alerts here."
            ),
            parse_mode="HTML",
        )
        return {"ok": True, "bot_username": me.username}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
