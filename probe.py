import asyncio
import json
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["KARBO_BOT_TOKEN"]
CHAT_ID = os.environ.get("PROBE_CHAT_ID")
USER_ID = os.environ.get("PROBE_USER_ID")
API_BASE = "https://api.karboai.com"


def _must(value: str | None, name: str) -> str:
    if value:
        return value
    raise RuntimeError(f"Missing required env var: {name}")


async def _read_first_member(session: aiohttp.ClientSession, chat_id: str) -> dict:
    async with session.get(
        f"{API_BASE}/bot/chat/{chat_id}/members",
        params={"limit": 1, "offset": 0},
    ) as response:
        body = await response.json()
        return body.get("items", [{}])[0]


async def main() -> None:
    chat_id = _must(CHAT_ID, "PROBE_CHAT_ID")
    user_id = _must(USER_ID, "PROBE_USER_ID")
    async with aiohttp.ClientSession(headers={"Bot-Token": TOKEN}) as session:
        first_member = await _read_first_member(session, chat_id)
        print("First member dump:")
        print(json.dumps(first_member, indent=2, ensure_ascii=False))
        print("---")

        async with session.get(f"{API_BASE}/bot/user/{user_id}") as response:
            print(f"\n[{response.status}] /bot/user/{user_id}")
            print((await response.text())[:2000])

        for query in [
            {"filter": "helpers"},
            {"filter": "staff"},
            {"role": "helper"},
            {"type": "helper"},
            {"include_roles": "true"},
            {"expand": "permissions"},
        ]:
            async with session.get(
                f"{API_BASE}/bot/chat/{chat_id}/members",
                params={**query, "limit": 1, "offset": 0},
            ) as response:
                body = await response.text()
                if response.status == 200:
                    items = json.loads(body).get("items", [{}])
                    first = items[0] if items else {}
                    print(f"\n[200] members {query}: first item keys = {list(first.keys())}")
                else:
                    print(f"\n[{response.status}] members {query}: {body[:200]}")

        for path in [
            f"/bot/chat/{chat_id}/permissions",
            f"/bot/chat/{chat_id}/roles",
            f"/bot/chat/{chat_id}/members/{user_id}",
            f"/bot/chat/{chat_id}/member/{user_id}",
            f"/bot/chat/{chat_id}/user/{user_id}",
        ]:
            async with session.get(f"{API_BASE}{path}") as response:
                body = await response.text()
                print(f"\n[{response.status}] {path}: {body[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
