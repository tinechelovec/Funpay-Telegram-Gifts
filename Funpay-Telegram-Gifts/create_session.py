import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client

if not hasattr(Client, "send_gift"):
    raise RuntimeError(
        "Установлен неподдерживаемый пакет 'pyrogram'. Нужен форк с поддержкой Stars.\n"
        "Используйте: pip uninstall -y pyrogram && pip install -U pyrofork tgcrypto"
    )

load_dotenv()
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

if not API_ID or not API_HASH:
    raise SystemExit("В .env должны быть API_ID и API_HASH для первичного логина.")

API_ID = int(API_ID)

TIMEOUT = 30
WARNING = "⚠️ Нет ответа 30 секунд. Попробуйте синхронизировать время на ПК и перезапустить скрипт."

async def main():
    app = Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions")
    started = False
    try:

        await asyncio.wait_for(app.start(), timeout=TIMEOUT)
        started = True

        me = await asyncio.wait_for(app.get_me(), timeout=TIMEOUT)
        bal = await asyncio.wait_for(app.get_stars_balance(), timeout=TIMEOUT)

        print(f"✅ Успешно вошли как {me.first_name} (ID: {me.id})")
        print(f"🌟 Текущий баланс звёзд: {bal}")

    except asyncio.TimeoutError:
        print(WARNING)
    finally:
        if started:
            try:
                await app.stop()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
