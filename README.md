# karbo-moderator

Простой моделатор-бот для KarboAI. На питоне, через офиц. SDK `karbo`.

Что умеет:
- варны и автокик по лимиту (`/warn`, `/unwarn`)
- кик `/kick`
- посмотреть варны `/warns`
- настройка лимита варнов `/setwarns N` (организатор)
- авто-кик за 18+ изображения (через nudenet)
- `/setnsfw N | on | off` - настройки NSFW в чате (организатор)
- `/leave` чтобы бот вышел из чата
- `/help`

`/warn`, `/unwarn`, `/kick` пока работают только реплаем на сообщение нарушителя. Mention'ы хочу добавить позже.

## Запуск

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# впиши KARBO_BOT_TOKEN в .env
python bot.py
```

Первый запуск ~30с потому что nudenet тащит ONNX-модель.

## .env

Минимум - KARBO_BOT_TOKEN. Остальное смотри в `.env.example`.
