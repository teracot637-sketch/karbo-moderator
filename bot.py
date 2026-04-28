import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from karbo import KarboBot, KarboBotWS, Message
from karbo.errors import ForbiddenError, KarboError

from storage import Storage


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("moderator")

TOKEN = os.environ["KARBO_BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "moderator.db")
WARN_LIMIT = int(os.environ.get("DEFAULT_WARN_LIMIT", "10"))


async def reply(bot, msg, text):
    try:
        await bot.send_message(msg.chat_id, text, reply_to=msg.message_id)
    except KarboError as e:
        log.warning("не смог ответить: %s", e)


async def cmd_warn(bot, bot_id, msg, storage, args):
    if not msg.reply_message_id:
        await reply(bot, msg, "Команда /warn должна быть ответом на сообщение нарушителя.")
        return
    try:
        target = await bot.get_message(msg.chat_id, msg.reply_message_id)
    except KarboError as e:
        log.warning("не достал реплай: %s", e)
        return
    target_id = target.user_id
    target_name = target.author.nickname if target.author else target_id

    if target_id == bot_id:
        await reply(bot, msg, "Себя предупредить я не дам.")
        return

    reason = " ".join(args).strip()
    count = await storage.add_warn(msg.chat_id, target_id, msg.user_id, reason, int(time.time()))

    if count < WARN_LIMIT:
        text = "%s получил предупреждение %d/%d." % (target_name, count, WARN_LIMIT)
        if reason:
            text += " Причина: " + reason
        await reply(bot, msg, text)
        return

    # лимит - кикаем
    try:
        await bot.kick_user(msg.chat_id, target_id)
        await storage.clear_warns(msg.chat_id, target_id)
        await reply(bot, msg, f"{target_name} получил {count}/{WARN_LIMIT} варнов и был кикнут.")
    except ForbiddenError:
        await reply(bot, msg, f"Не могу кикнуть {target_name}: нет прав.")
    except KarboError as e:
        await reply(bot, msg, f"Ошибка кика: {e}")


async def main():
    storage = Storage(DB_PATH)
    await storage.init()

    async with KarboBot(TOKEN) as bot:
        ws = KarboBotWS(TOKEN)
        me = await bot.get_me()
        bot_id = me.bot_id
        log.info("Бот онлайн: %s id=%s", me.name, bot_id)

        @ws.on_message
        async def on_message(msg: Message):
            if msg.user_id == bot_id:
                return
            content = (msg.content or "").strip()
            log.info("MSG %s: %r", msg.user_id, content[:80])

            if content.startswith("/"):
                parts = content[1:].split()
                if not parts:
                    return
                cmd = parts[0].lower()
                args = parts[1:]
                try:
                    if cmd == "warn":
                        await cmd_warn(bot, bot_id, msg, storage, args)
                except Exception as e:
                    log.exception("ошибка в команде %s: %s", cmd, e)

        log.info("Подключаюсь к WebSocket...")
        await ws.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
