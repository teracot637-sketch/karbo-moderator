import asyncio
import logging
import os

from dotenv import load_dotenv
from karbo import KarboBot, KarboBotWS, Message


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("moderator")

TOKEN = os.environ["KARBO_BOT_TOKEN"]


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
            nick = msg.author.nickname if msg.author else "?"
            log.info(
                "MSG chat=%s user=%s nick=%r: %r",
                msg.chat_id, msg.user_id, nick,
                (msg.content or "")[:80],
            )

        log.info("Подключаюсь к WebSocket...")
        await ws.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
