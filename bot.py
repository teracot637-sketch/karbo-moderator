import asyncio
import os

from dotenv import load_dotenv
from karbo import KarboBot


load_dotenv()

TOKEN = os.environ["KARBO_BOT_TOKEN"]


async def main():
    async with KarboBot(TOKEN) as bot:
        me = await bot.get_me()
        print("я", me.name, me.bot_id)


if __name__ == "__main__":
    asyncio.run(main())
