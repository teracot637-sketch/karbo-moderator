import asyncio
import logging
import os

from dotenv import load_dotenv
from karbo import KarboBot, KarboBotWS, Message
from karbo.errors import KarboError


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("moderator")

TOKEN = os.environ["KARBO_BOT_TOKEN"]
WARN_LIMIT = 3

# пока в памяти, потом в sqlite перенесу
# (chat_id, user_id) -> кол-во варнов
warns = {}


async def reply(bot, msg, text):
    try:
        await bot.send_message(msg.chat_id, text, reply_to=msg.message_id)
    except KarboError as e:
        log.warning("не смог ответить: %s", e)


async def cmd_warn(bot, msg):
    if not msg.reply_message_id:
        await reply(bot, msg, "Команда /warn должна быть ответом на сообщение.")
        return
    try:
        target = await bot.get_message(msg.chat_id, msg.reply_message_id)
    except KarboError as e:
        log.warning("не достал реплай: %s", e)
        return
    target_id = target.user_id
    target_name = target.author.nickname if target.author else target_id

    key = (msg.chat_id, target_id)
    warns[key] = warns.get(key, 0) + 1
    count = warns[key]

    await reply(bot, msg, f"{target_name} получил предупреждение {count}/{WARN_LIMIT}.")


async def main():
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

            if content.startswith("/warn"):
                await cmd_warn(bot, msg)

        log.info("Подключаюсь к WebSocket...")
        await ws.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
