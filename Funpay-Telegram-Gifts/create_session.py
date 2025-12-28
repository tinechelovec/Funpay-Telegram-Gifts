import asyncio
import os
import getpass
from dotenv import load_dotenv

from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PhoneNumberUnoccupied,
)

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


def ask_phone() -> str:
    print("Привет! Это файл для создания сессии.")
    print("Чтобы создать сессию, нужно ввести номер телефона от Telegram.\n")

    while True:
        phone = input("📱 Введите номер телефона (пример: +79991234567): ").strip()
        phone = phone.replace(" ", "")
        if not phone:
            print("❌ Номер не может быть пустым.\n")
            continue

        confirm = input(f"Вы ввели номер: {phone}. Это верно? (да/нет): ").strip().lower()
        if confirm in ("да", "д", "y", "yes"):
            return phone
        print("Ок, давайте введём номер заново.\n")


def ask_code() -> str:
    while True:
        code = input("🔐 Введите код из Telegram: ").strip().replace(" ", "")
        if code:
            return code
        print("❌ Код не может быть пустым.\n")


async def main():
    app = Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions")

    await app.connect()

    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
        username = f"@{me.username}" if me.username else f"{me.first_name} (без username)"
        print("✅ Сессия уже существует (вход выполнен).")
        print(f"👤 Аккаунт: {username} | ID: {me.id}")
        print(f"🌟 Кол-во звёзд: {bal}")
        await app.disconnect()
        return
    except Exception:
        pass

    phone = ask_phone()

    try:
        sent = await app.send_code(phone)
    except PhoneNumberInvalid:
        print("❌ Неверный формат номера телефона. Перезапустите и введите номер правильно.")
        await app.disconnect()
        return
    except PhoneNumberUnoccupied:
        print("❌ Этот номер не зарегистрирован в Telegram.")
        await app.disconnect()
        return

    code = ask_code()

    try:
        await app.sign_in(phone_number=phone, phone_code_hash=sent.phone_code_hash, phone_code=code)
    except PhoneCodeInvalid:
        print("❌ Код неверный. Перезапустите скрипт и попробуйте снова.")
        await app.disconnect()
        return
    except PhoneCodeExpired:
        print("❌ Код устарел. Перезапустите скрипт и запросите новый код.")
        await app.disconnect()
        return
    except SessionPasswordNeeded:
        pwd = getpass.getpass("🔒 Включена двухэтапная проверка (2FA). Введите пароль: ")
        await app.check_password(pwd)

    me = await app.get_me()
    bal = await app.get_stars_balance()

    username = f"@{me.username}" if me.username else f"{me.first_name} (без username)"
    print("\n✅ Сессия успешно создана и сохранена в папке sessions.")
    print(f"👤 Ник/аккаунт: {username} | ID: {me.id}")
    print(f"🌟 Кол-во звёзд: {bal}")

    await app.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
