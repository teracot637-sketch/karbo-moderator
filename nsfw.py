import asyncio
import logging
import os
import tempfile

import aiohttp


log = logging.getLogger("moderator.nsfw")

# на эти классы реагируем как на 18+
# nudenet возвращает ещё всякую фигню типа FACE_FEMALE - игнорим
EXPLICIT_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "ANUS_EXPOSED",
}

FETCH_TIMEOUT = 15  # сек, на скачивание картинки


class NSFWDetector:
    def __init__(self, threshold=0.6):
        self.threshold = threshold
        self._detector = None
        self.ready = False
        # nudenet тащит ~30мб onnx-модель при первом запуске. Жёсткий импорт
        # внутри try чтобы если не поставлена - не падал весь бот, а просто
        # nsfw отключился
        try:
            from nudenet import NudeDetector
            self._detector = NudeDetector()
            self.ready = True
            log.info("NudeDetector загружен (порог=%.2f)", threshold)
        except Exception as e:
            log.warning("nudenet не доступен, NSFW отключён: %s", e)

    async def is_explicit(self, image_urls):
        if not self.ready:
            return False
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in image_urls:
                try:
                    if await self._check_url(session, url):
                        return True
                except Exception as e:
                    # 404 / битая картинка / отвалился сервер - пофиг, идём дальше
                    log.warning("не смог проверить %s: %s", url, e)
        return False

    async def _check_url(self, session, url):
        # качаем во временный файл (nudenet хочет путь, а не bytes)
        async with session.get(url) as resp:
            if resp.status != 200:
                return False
            data = await resp.read()

        # пробуем угадать расширение по урлу, если не получилось - jpg
        suffix = ".jpg"
        clean = url.split("?", 1)[0]
        if "." in clean.split("/")[-1]:
            suffix = "." + clean.rsplit(".", 1)[-1]

        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        path = tmp.name

        try:
            # detect синхронный, так что в executor чтобы не лочить event loop
            loop = asyncio.get_running_loop()
            detections = await loop.run_in_executor(None, self._detector.detect, path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass  # ну и хрен с ним

        for det in detections or []:
            cls = det.get("class")
            score = float(det.get("score") or 0)
            if cls in EXPLICIT_CLASSES and score >= self.threshold:
                log.info("NSFW: класс=%s скор=%.2f", cls, score)
                return True
        return False
