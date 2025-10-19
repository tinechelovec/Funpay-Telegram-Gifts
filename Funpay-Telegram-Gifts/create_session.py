import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler

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

async def wait_first_message(app: Client, timeout: int) -> bool:
    first_msg_event = asyncio.Event()

    async def on_msg(_, __):
        if not first_msg_event.is_set():
            first_msg_event.set()

    handler = MessageHandler(on_msg, filters.incoming)
    app.add_handler(handler)

    try:
        await asyncio.wait_for(first_msg_event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        app.remove_handler(handler)

async def main():
    app = Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions")
    started = False
    try:
        await asyncio.wait_for(app.start(), timeout=TIMEOUT)
        started = True

        got_first = await wait_first_message(app, TIMEOUT)
        if not got_first:
            print(WARNING)

        me = await asyncio.wait_for(app.get_me(), timeout=TIMEOUT)
        bal = await asyncio.wait_for(app.get_stars_balance(), timeout=TIMEOUT)

        print(f"✅ Успешно вошли как {me.first_name} (ID: {me.id})")
        print(f"🌟 Текущий баланс звёзд: {bal}")

    except asyncio.TimeoutError:
        print("⚠️ Нет ответа 30 секунд. Попробуйте синхронизировать время на ПК и перезапустить скрипт.")
    finally:
        if started:
            try:
                await app.stop()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
