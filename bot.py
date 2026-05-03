import asyncio
import logging
import os
import re
import time
from dataclasses import replace

from dotenv import load_dotenv
from karbo import KarboBot, KarboBotWS, Message
from karbo.errors import ForbiddenError, KarboError

from nsfw import NSFWDetector
from storage import Storage


load_dotenv()

# логи
log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level_name, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("moderator")

# конфиг из .env
TOKEN = os.environ["KARBO_BOT_TOKEN"]
OWNER = (os.environ.get("BOT_OWNER_ID") or "").strip()
DB_PATH = os.environ.get("DB_PATH", "moderator.db")
DEFAULT_LIMIT = int(os.environ.get("DEFAULT_WARN_LIMIT", "10"))
NSFW_ON = os.environ.get("NSFW_ENABLED", "1") == "1"
NSFW_THR = float(os.environ.get("NSFW_THRESHOLD", "0.6"))
NSFW_LIMIT = int(os.environ.get("NSFW_WARN_LIMIT", "3"))
HELPER_MIN = int(os.environ.get("HELPER_ROLE_MIN", "1"))
ORG_MIN = int(os.environ.get("ORGANIZER_ROLE_MIN", "2"))
ROLE_TTL = float(os.environ.get("BOT_ROLE_TTL", "300"))
PREFIX = (os.environ.get("CMD_PREFIX") or "/").strip() or "/"

# кэш роли бота на 5 мин, иначе апи задрочим
# chat_id -> (role, expires_at)
_role_cache = {}

# uuid из mention
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def msg_role(msg):
    if not msg.author or msg.author.role is None:
        return 0
    return msg.author.role


def msg_name(msg):
    if msg.author and msg.author.nickname:
        return msg.author.nickname
    return msg.user_id


async def fetch_role(bot, chat_id, user_id):
    # тащим всех мемберов и ищем кого надо. tупо но работает
    # 200 это максимум на запрос вроде
    offset = 0
    try:
        while True:
            members = await bot.get_chat_members(chat_id, limit=200, offset=offset)
            if not members:
                return 0
            for m in members:
                if m.user_id == user_id:
                    return m.role
            if len(members) < 200:
                return 0
            offset += 200
    except KarboError as e:
        log.warning("не достал роль %s/%s: %s", chat_id, user_id, e)
        return 0


async def get_bot_role(bot, bot_id, chat_id):
    cached = _role_cache.get(chat_id)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]
    role = await fetch_role(bot, chat_id, bot_id)
    _role_cache[chat_id] = (role, now + ROLE_TTL)
    return role


async def reply(bot, msg, text):
    try:
        await bot.send_message(msg.chat_id, text, reply_to=msg.message_id)
    except KarboError as e:
        log.warning("не смог ответить: %s", e)


async def get_reply_target(bot, msg):
    # тащим сообщение на которое реплай чтобы узнать кому варн
    if not msg.reply_message_id:
        return None
    try:
        target = await bot.get_message(msg.chat_id, msg.reply_message_id)
    except KarboError as e:
        log.warning("не достал реплай: %s", e)
        return None
    name = target.author.nickname if target.author else target.user_id
    return target.user_id, name


async def get_mention_target(bot, msg, args):
    """
    ищем в args либо uuid либо @ник.
    возвращает (target, оставшиеся args, статус)
    статусы: ok / no_target / not_found
    """
    target_idx = -1
    target_kind = None
    target_value = None

    for i, raw in enumerate(args):
        s = raw.strip().strip(".,:;!?()[]{}<>\"'")
        if not s:
            continue
        m = UUID_RE.search(s)
        if m:
            target_kind = "uuid"
            target_value = m.group(0)
            target_idx = i
            break
        if s.startswith("@") and len(s) > 1:
            target_kind = "nick"
            target_value = s[1:]
            target_idx = i
            break

    if target_value is None:
        return None, args, "no_target"

    # надо вытащить мемберов и поискать. да, опять. api такое.
    # TODO: может потом закэшить хотя бы на 30 сек, пока пофиг
    members = []
    offset = 0
    try:
        while True:
            batch = await bot.get_chat_members(msg.chat_id, limit=200, offset=offset)
            if not batch:
                break
            members.extend(batch)
            if len(batch) < 200:
                break
            offset += 200
    except KarboError as e:
        log.warning("не достал участников: %s", e)
        return None, args, "not_found"

    found = None
    if target_kind == "uuid":
        for m in members:
            if m.user_id.lower() == target_value.lower():
                found = (m.user_id, m.nickname)
                break
    else:
        wanted = target_value.lower()
        for m in members:
            # ник может быть None если юзер без ника, ну и пофиг
            if m.nickname and m.nickname.lower() == wanted:
                found = (m.user_id, m.nickname)
                break

    if not found:
        return None, args, "not_found"

    rest = [a for j, a in enumerate(args) if j != target_idx]
    return found, rest, "ok"


# ===========================================================
#                     команды
# ===========================================================

async def cmd_warn(bot, bot_id, msg, storage, args):
    # бот сам должен быть помощником и автор тоже
    if await get_bot_role(bot, bot_id, msg.chat_id) < HELPER_MIN:
        return
    if msg_role(msg) < HELPER_MIN:
        return

    # цель: реплай или mention
    if msg.reply_message_id:
        t = await get_reply_target(bot, msg)
        if not t:
            await reply(bot, msg, "Не нашёл сообщение нарушителя.")
            return
        target_id, target_name = t
        reason_args = args
    else:
        found, reason_args, status = await get_mention_target(bot, msg, args)
        if not found:
            if status == "no_target":
                await reply(bot, msg, "Команда /warn должна быть ответом или содержать упоминание.")
            else:
                await reply(bot, msg, "Не нашёл такого пользователя.")
            return
        target_id, target_name = found

    if target_id == bot_id:
        await reply(bot, msg, "Себя предупредить я не дам.")
        return

    reason = " ".join(reason_args).strip()
    count = await storage.add_warn(msg.chat_id, target_id, msg.user_id, reason, int(time.time()))
    limit = await storage.get_warn_limit(msg.chat_id)

    if count < limit:
        text = "%s получил предупреждение %d/%d." % (target_name, count, limit)
        if reason:
            text += " Причина: " + reason
        await reply(bot, msg, text)
        return

    # лимит - кикаем
    try:
        await bot.kick_user(msg.chat_id, target_id)
        await storage.clear_warns(msg.chat_id, target_id)
        await storage.clear_nsfw_warns(msg.chat_id, target_id)
        await reply(bot, msg, f"{target_name} получил {count}/{limit} варнов и был кикнут.")
    except ForbiddenError:
        await reply(bot, msg, f"Не могу кикнуть {target_name}: нет прав.")
    except KarboError as e:
        await reply(bot, msg, f"Ошибка кика: {e}")


async def cmd_unwarn(bot, bot_id, msg, storage, args):
    if await get_bot_role(bot, bot_id, msg.chat_id) < HELPER_MIN:
        return
    if msg_role(msg) < HELPER_MIN:
        return

    if msg.reply_message_id:
        t = await get_reply_target(bot, msg)
        if not t:
            await reply(bot, msg, "Не нашёл сообщение пользователя.")
            return
        target_id, target_name = t
    else:
        found, _rest, status = await get_mention_target(bot, msg, args)
        if not found:
            if status == "no_target":
                await reply(bot, msg, "Команда /unwarn должна быть ответом или содержать упоминание.")
            else:
                await reply(bot, msg, "Не нашёл такого пользователя.")
            return
        target_id, target_name = found

    remaining = await storage.remove_last_warn(msg.chat_id, target_id)
    if remaining < 0:
        await reply(bot, msg, f"У {target_name} нет активных варнов.")
        return
    limit = await storage.get_warn_limit(msg.chat_id)
    await reply(bot, msg, f"С {target_name} снят варн. Осталось: {remaining}/{limit}.")


async def cmd_kick(bot, bot_id, msg, storage, args):
    # тоже самое почти как warn только без счётчика. лень выносить общий код
    if await get_bot_role(bot, bot_id, msg.chat_id) < HELPER_MIN:
        return
    if msg_role(msg) < HELPER_MIN:
        return

    if msg.reply_message_id:
        t = await get_reply_target(bot, msg)
        if not t:
            await reply(bot, msg, "Не нашёл сообщение нарушителя.")
            return
        target_id, target_name = t
        reason_args = args
    else:
        found, reason_args, status = await get_mention_target(bot, msg, args)
        if not found:
            if status == "no_target":
                await reply(bot, msg, "Команда /kick должна быть ответом или содержать упоминание.")
            else:
                await reply(bot, msg, "Не нашёл такого пользователя.")
            return
        target_id, target_name = found

    if target_id == bot_id:
        await reply(bot, msg, "Меня кикнуть не получится.")
        return

    reason = " ".join(reason_args).strip()
    try:
        await bot.kick_user(msg.chat_id, target_id)
        await storage.clear_warns(msg.chat_id, target_id)
        await storage.clear_nsfw_warns(msg.chat_id, target_id)
        text = f"{target_name} кикнут."
        if reason:
            text += " Причина: " + reason
        await reply(bot, msg, text)
    except ForbiddenError:
        await reply(bot, msg, f"Не могу кикнуть {target_name}: нет прав.")
    except KarboError as e:
        await reply(bot, msg, f"Ошибка кика: {e}")


async def cmd_setwarns(bot, bot_id, msg, storage, args):
    # только организатор
    if msg_role(msg) < ORG_MIN:
        return
    if not args or not args[0].isdigit():
        await reply(bot, msg, "Использование: /setwarns <число от 1 до 100>")
        return
    n = int(args[0])
    if n < 1 or n > 100:
        await reply(bot, msg, "Число должно быть от 1 до 100.")
        return
    await storage.set_warn_limit(msg.chat_id, n)
    await reply(bot, msg, f"Лимит варнов для этого чата: {n}.")


async def cmd_setnsfw(bot, bot_id, msg, storage, args):
    if msg_role(msg) < ORG_MIN:
        return
    if not args:
        await reply(bot, msg, "Использование: /setnsfw <число 1-100> | on | off")
        return

    a = args[0].lower()
    # принимаем on/off на разных раскладках, ну а вдруг
    if a in ("off", "disable", "выкл"):
        await storage.set_nsfw_enabled(msg.chat_id, False)
        await reply(bot, msg, "Авто-модерация 18+ отключена в этом чате.")
        return
    if a in ("on", "enable", "вкл"):
        await storage.set_nsfw_enabled(msg.chat_id, True)
        limit, _ = await storage.get_nsfw_config(msg.chat_id, NSFW_LIMIT)
        await reply(bot, msg, f"Авто-модерация 18+ включена. Лимит: {limit}.")
        return
    if a.isdigit():
        n = int(a)
        if n < 1 or n > 100:
            await reply(bot, msg, "Число должно быть от 1 до 100.")
            return
        await storage.set_nsfw_limit(msg.chat_id, n)
        await reply(bot, msg, f"Лимит NSFW-страйков для этого чата: {n}. Авто-модерация включена.")
        return

    await reply(bot, msg, "Использование: /setnsfw <число 1-100> | on | off")


async def cmd_warns(bot, bot_id, msg, storage, args):
    target_id = None
    target_name = "Пользователь"
    if msg.reply_message_id:
        t = await get_reply_target(bot, msg)
        if t:
            target_id, target_name = t
    elif args:
        found, _rest, status = await get_mention_target(bot, msg, args)
        if found:
            target_id, target_name = found
        elif status == "not_found":
            await reply(bot, msg, "Не нашёл такого пользователя.")
            return
    if not target_id:
        # ну ок, показываем свои
        target_id = msg.user_id
        target_name = msg_name(msg)

    count = await storage.count_warns(msg.chat_id, target_id)
    limit = await storage.get_warn_limit(msg.chat_id)
    nsfw_count = await storage.count_nsfw_warns(msg.chat_id, target_id)
    nsfw_limit, nsfw_enabled = await storage.get_nsfw_config(msg.chat_id, NSFW_LIMIT)
    if nsfw_enabled:
        nsfw_part = f"{nsfw_count}/{nsfw_limit} NSFW"
    else:
        nsfw_part = "NSFW-авто-модерация: выкл"
    await reply(bot, msg, f"{target_name}: {count}/{limit} варнов, {nsfw_part}.")


async def cmd_leave(bot, bot_id, msg, storage, args):
    is_owner = bool(OWNER) and msg.user_id == OWNER
    if not (is_owner or msg_role(msg) >= ORG_MIN):
        return
    await reply(bot, msg, "Выхожу из чата.")
    try:
        await bot.leave_chat(msg.chat_id)
        _role_cache.pop(msg.chat_id, None)  # кэш сбросить тут
    except KarboError as e:
        log.warning("не смог выйти: %s", e)


HELP_TEXT = (
    "Команды модератора (префикс {p})\n\n"
    "{p}warn [причина] - выдать варн (reply или упоминание). При достижении лимита - авто-кик.\n"
    "{p}unwarn - снять последний варн (reply или упоминание).\n"
    "{p}kick [причина] - кикнуть (reply или упоминание).\n"
    "{p}warns - показать число варнов (свои или reply на юзера).\n"
    "{p}setwarns N - установить лимит варнов в чате (только организатор).\n"
    "{p}setnsfw N | on | off - лимит NSFW-страйков, вкл/выкл авто-18+ (организатор).\n"
    "{p}leave - бот выходит из чата (организатор или владелец бота).\n"
    "{p}help - эта справка.\n\n"
    "Авто-модерация: за каждое 18+ изображение - страйк, по достижении лимита - кик."
)


async def cmd_help(bot, bot_id, msg, storage, args):
    await reply(bot, msg, HELP_TEXT.format(p=PREFIX))


# =============================================================
#                       главный цикл
# =============================================================

async def main():
    storage = Storage(DB_PATH, DEFAULT_LIMIT)
    await storage.init()

    nsfw = NSFWDetector(threshold=NSFW_THR) if NSFW_ON else None

    async with KarboBot(TOKEN) as bot:
        ws = KarboBotWS(TOKEN)
        me = await bot.get_me()
        bot_id = me.bot_id
        log.info(
            "Бот онлайн: name=%r id=%s status=%s owner=%s",
            me.name, bot_id, me.status, OWNER or "<не задан>",
        )

        @ws.on_message
        async def on_message(msg: Message):
            # бывает что user_id пустой а user сидит в author. подменяем
            if not msg.user_id and msg.author and msg.author.user_id:
                msg = replace(msg, user_id=msg.author.user_id)

            # себя и не-текст игнорим
            if msg.user_id == bot_id:
                return
            if msg.type != 0:
                return

            nick = msg.author.nickname if msg.author else "?"
            role = msg.author.role if msg.author else 0
            log.info(
                "MSG chat=%s user=%s nick=%r role=%s: %r",
                msg.chat_id, msg.user_id, nick, role,
                (msg.content or "")[:80],
            )
            # print("DEBUG", msg.images)  # пригодится если nsfw сглючит

            content = (msg.content or "").strip()

            # команды
            if content.startswith(PREFIX):
                parts = content[len(PREFIX):].split()
                if not parts:
                    return
                cmd = parts[0].lower().split("@", 1)[0]
                args = parts[1:]
                try:
                    if cmd == "warn":
                        await cmd_warn(bot, bot_id, msg, storage, args)
                    elif cmd == "unwarn":
                        await cmd_unwarn(bot, bot_id, msg, storage, args)
                    elif cmd == "kick":
                        await cmd_kick(bot, bot_id, msg, storage, args)
                    elif cmd == "setwarns":
                        await cmd_setwarns(bot, bot_id, msg, storage, args)
                    elif cmd == "setnsfw":
                        await cmd_setnsfw(bot, bot_id, msg, storage, args)
                    elif cmd == "warns":
                        await cmd_warns(bot, bot_id, msg, storage, args)
                    elif cmd == "leave":
                        await cmd_leave(bot, bot_id, msg, storage, args)
                    elif cmd == "help":
                        await cmd_help(bot, bot_id, msg, storage, args)
                    else:
                        # хз что это, мимо
                        return
                except Exception as e:
                    log.exception("ошибка в команде %s: %s", cmd, e)
                return

            # не команда - картинки на nsfw
            if nsfw and nsfw.ready and msg.images:
                asyncio.create_task(handle_nsfw(bot, bot_id, msg, storage, nsfw))

        log.info("Подключаюсь к WebSocket...")
        await ws.run_forever()


async def handle_nsfw(bot, bot_id, msg, storage, nsfw):
    if not msg.images:
        return

    limit, enabled = await storage.get_nsfw_config(msg.chat_id, NSFW_LIMIT)
    if not enabled:
        return

    # тут без try падало пару раз когда картинка битая. теперь ловим всё
    try:
        explicit = await nsfw.is_explicit(msg.images)
    except Exception as e:
        log.warning("ошибка проверки NSFW: %s", e)
        return
    if not explicit:
        return

    # бот должен быть помощником чтобы кикнуть
    if await get_bot_role(bot, bot_id, msg.chat_id) < HELPER_MIN:
        return

    name = msg_name(msg)
    count = await storage.add_nsfw_warn(msg.chat_id, msg.user_id, int(time.time()))

    if count < limit:
        await reply(bot, msg, f"{name}, 18+ контент запрещён. Предупреждение {count}/{limit}.")
        return

    try:
        await bot.kick_user(msg.chat_id, msg.user_id)
        await storage.clear_warns(msg.chat_id, msg.user_id)
        await storage.clear_nsfw_warns(msg.chat_id, msg.user_id)
        await reply(bot, msg, f"{name} кикнут за 18+ контент ({count}/{limit}).")
    except ForbiddenError:
        return
    except KarboError as e:
        log.warning("не смог кикнуть за NSFW: %s", e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
