import aiosqlite


# обёртка над sqlite. варны - по строке на варн (так проще снимать последний)
class Storage:
    def __init__(self, db_path):
        self.db_path = db_path

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

    async def clear_warns(self, chat_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            await db.commit()
