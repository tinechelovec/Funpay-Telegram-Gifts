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

async def main():
    async with Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions") as app:
        me = await app.get_me()
        bal = await app.get_stars_balance()
        print(f"✅ Успешно вошли как {me.first_name} (ID: {me.id})")
        print(f"🌟 Текущий баланс звёзд: {bal}")

if __name__ == "__main__":
    asyncio.run(main())
