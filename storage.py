import aiosqlite


# обёртка над sqlite. варны - по строке на варн (так проще снимать последний)
class Storage:
    def __init__(self, db_path, default_warn_limit=10):
        self.db_path = db_path
        self.default_limit = default_warn_limit

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warns (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id    TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    issuer_id  TEXT NOT NULL,
                    reason     TEXT,
                    created_at INTEGER NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_warns_chat_user ON warns(chat_id, user_id)"
            )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS nsfw_warns (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id    TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_nsfw_warns_chat_user ON nsfw_warns(chat_id, user_id)"
            )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_config (
                    chat_id    TEXT PRIMARY KEY,
                    warn_limit INTEGER
                )
            """)
            await db.commit()

    async def add_warn(self, chat_id, user_id, issuer_id, reason, ts):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO warns (chat_id, user_id, issuer_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, issuer_id, reason, ts),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT COUNT(*) FROM warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            return row[0] if row else 0

    async def count_warns(self, chat_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            return row[0] if row else 0

    async def clear_warns(self, chat_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            await db.commit()

    async def remove_last_warn(self, chat_id, user_id):
        # снимаем последний (самый свежий по id)
        # -1 если варнов нет, иначе сколько осталось
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT id FROM warns WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                return -1
            await db.execute("DELETE FROM warns WHERE id=?", (row[0],))
            await db.commit()
            cur = await db.execute(
                "SELECT COUNT(*) FROM warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            count = await cur.fetchone()
            return count[0] if count else 0

    # ---- nsfw страйки ----
    # отдельный счётчик чтобы случайный варн не сбрасывал nsfw и наоборот

    async def add_nsfw_warn(self, chat_id, user_id, ts):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO nsfw_warns (chat_id, user_id, created_at) VALUES (?, ?, ?)",
                (chat_id, user_id, ts),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT COUNT(*) FROM nsfw_warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            return row[0] if row else 0

    async def count_nsfw_warns(self, chat_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM nsfw_warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            return row[0] if row else 0

    async def clear_nsfw_warns(self, chat_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM nsfw_warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            await db.commit()

    # ---- настройки чата ----

    async def get_warn_limit(self, chat_id):
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT warn_limit FROM chat_config WHERE chat_id=?",
                (chat_id,),
            )
            row = await cur.fetchone()
            if not row or row[0] is None:
                return self.default_limit
            return row[0]

    async def set_warn_limit(self, chat_id, limit):
        # upsert: если есть запись - апдейтим, иначе вставляем
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO chat_config (chat_id, warn_limit) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET warn_limit=excluded.warn_limit",
                (chat_id, limit),
            )
            await db.commit()
