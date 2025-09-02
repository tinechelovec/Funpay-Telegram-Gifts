import asyncio
from pyrogram import Client
import os
from dotenv import load_dotenv

load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

async def main():
    async with Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions") as app:
        me = await app.get_me()
        print(f"✅ Успешно вошли как {me.first_name} (ID: {me.id})")

if __name__ == "__main__":
    asyncio.run(main())
