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


def msg_name(msg):
    if msg.author and msg.author.nickname:
        return msg.author.nickname
    return msg.user_id


async def reply(bot, msg, text):
    try:
        await bot.send_message(msg.chat_id, text, reply_to=msg.message_id)
    except KarboError as e:
        log.warning("не смог ответить: %s", e)


async def get_reply_target(bot, msg):
    if not msg.reply_message_id:
        return None
    try:
        target = await bot.get_message(msg.chat_id, msg.reply_message_id)
    except KarboError as e:
        log.warning("не достал реплай: %s", e)
        return None
    name = target.author.nickname if target.author else target.user_id
    return target.user_id, name


async def cmd_warn(bot, bot_id, msg, storage, args):
    t = await get_reply_target(bot, msg)
    if not t:
        await reply(bot, msg, "Команда /warn должна быть ответом на сообщение нарушителя.")
        return
    target_id, target_name = t

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

    try:
        await bot.kick_user(msg.chat_id, target_id)
        await storage.clear_warns(msg.chat_id, target_id)
        await reply(bot, msg, f"{target_name} получил {count}/{WARN_LIMIT} варнов и был кикнут.")
    except ForbiddenError:
        await reply(bot, msg, f"Не могу кикнуть {target_name}: нет прав.")
    except KarboError as e:
        await reply(bot, msg, f"Ошибка кика: {e}")


async def cmd_kick(bot, bot_id, msg, storage, args):
    # тоже самое почти как warn только без счётчика
    t = await get_reply_target(bot, msg)
    if not t:
        await reply(bot, msg, "Команда /kick должна быть ответом на сообщение нарушителя.")
        return
    target_id, target_name = t

    if target_id == bot_id:
        await reply(bot, msg, "Меня кикнуть не получится.")
        return

    reason = " ".join(args).strip()
    try:
        await bot.kick_user(msg.chat_id, target_id)
        await storage.clear_warns(msg.chat_id, target_id)
        text = f"{target_name} кикнут."
        if reason:
            text += " Причина: " + reason
        await reply(bot, msg, text)
    except ForbiddenError:
        await reply(bot, msg, f"Не могу кикнуть {target_name}: нет прав.")
    except KarboError as e:
        await reply(bot, msg, f"Ошибка кика: {e}")


async def cmd_unwarn(bot, bot_id, msg, storage, args):
    t = await get_reply_target(bot, msg)
    if not t:
        await reply(bot, msg, "Команда /unwarn должна быть ответом на сообщение пользователя.")
        return
    target_id, target_name = t

    remaining = await storage.remove_last_warn(msg.chat_id, target_id)
    if remaining < 0:
        await reply(bot, msg, f"У {target_name} нет активных варнов.")
        return
    await reply(bot, msg, f"С {target_name} снят варн. Осталось: {remaining}/{WARN_LIMIT}.")


async def cmd_warns(bot, bot_id, msg, storage, args):
    target_id = None
    target_name = "Пользователь"
    if msg.reply_message_id:
        t = await get_reply_target(bot, msg)
        if t:
            target_id, target_name = t
    if not target_id:
        # ну ок, показываем свои
        target_id = msg.user_id
        target_name = msg_name(msg)

    count = await storage.count_warns(msg.chat_id, target_id)
    await reply(bot, msg, f"{target_name}: {count}/{WARN_LIMIT} варнов.")


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
                    elif cmd == "unwarn":
                        await cmd_unwarn(bot, bot_id, msg, storage, args)
                    elif cmd == "kick":
                        await cmd_kick(bot, bot_id, msg, storage, args)
                    elif cmd == "warns":
                        await cmd_warns(bot, bot_id, msg, storage, args)
                except Exception as e:
                    log.exception("ошибка в команде %s: %s", cmd, e)

        log.info("Подключаюсь к WebSocket...")
        await ws.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
