import asyncio
import os
import sys
import stat
import getpass
import traceback
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.path.join(BASE_DIR, "sessions")

def pause_exit(msg: str = "\nНажмите Enter, чтобы закрыть...") -> None:
    try:
        input(msg)
    except Exception:
        pass

def _ensure_sessions_dir() -> None:
    os.makedirs(WORKDIR, exist_ok=True)
    try:
        os.chmod(WORKDIR, 0o700)
    except Exception:
        pass


def _print_debug_info() -> None:
    print("────────────────────────────────────────")
    print("Debug info:")
    print("Python:", sys.version.replace("\n", " "))
    print("CWD:", os.getcwd())
    print("Script dir:", BASE_DIR)
    print("Sessions dir:", WORKDIR)
    print("Sessions exists:", os.path.isdir(WORKDIR))
    try:
        mode = stat.S_IMODE(os.stat(WORKDIR).st_mode)
        print("Sessions perms (oct):", oct(mode))
    except Exception:
        pass
    try:
        print("Can write sessions:", os.access(WORKDIR, os.W_OK))
    except Exception:
        pass
    print("────────────────────────────────────────\n")

def ask_phone() -> str:
    print("Привет! Это файл для создания сессии Pyrogram/pyrofork.")
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

async def main() -> int:
    _ensure_sessions_dir()
    _print_debug_info()

    app = Client("stars", api_id=API_ID, api_hash=API_HASH, workdir=WORKDIR)

    try:
        await app.connect()
    except Exception as e:
        print("❌ Не удалось подключиться (app.connect):", repr(e))
        traceback.print_exc()
        pause_exit()
        return 1

    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
        username = f"@{me.username}" if getattr(me, "username", None) else f"{me.first_name} (без username)"
        print("✅ Сессия уже существует (вход выполнен).")
        print(f"👤 Аккаунт: {username} | ID: {me.id}")
        print(f"🌟 Кол-во звёзд: {bal}")
        await app.disconnect()
        pause_exit("\nНажмите Enter, чтобы закрыть...")
        return 0
    except Exception:
        pass

    phone = ask_phone()

    try:
        sent = await app.send_code(phone)
    except PhoneNumberInvalid:
        print("❌ Неверный формат номера телефона. Перезапустите и введите номер правильно.")
        await app.disconnect()
        pause_exit()
        return 1
    except PhoneNumberUnoccupied:
        print("❌ Этот номер не зарегистрирован в Telegram.")
        await app.disconnect()
        pause_exit()
        return 1
    except Exception as e:
        print("❌ Ошибка при отправке кода:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        pause_exit()
        return 1

    code = ask_code()

    try:
        await app.sign_in(
            phone_number=phone,
            phone_code_hash=sent.phone_code_hash,
            phone_code=code,
        )
    except PhoneCodeInvalid:
        print("❌ Код неверный. Перезапустите скрипт и попробуйте снова.")
        await app.disconnect()
        pause_exit()
        return 1
    except PhoneCodeExpired:
        print("❌ Код устарел. Перезапустите скрипт и запросите новый код.")
        await app.disconnect()
        pause_exit()
        return 1
    except SessionPasswordNeeded:
        pwd = getpass.getpass("🔒 Включена двухэтапная проверка (2FA). Введите пароль: ")
        try:
            await app.check_password(pwd)
        except Exception as e:
            print("❌ Ошибка 2FA пароля:", repr(e))
            traceback.print_exc()
            await app.disconnect()
            pause_exit()
            return 1
    except Exception as e:
        print("❌ НЕОЖИДАННАЯ ОШИБКА при sign_in:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        pause_exit()
        return 1

    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
    except Exception as e:
        print("⚠️ Вход вроде прошёл, но не удалось получить профиль/баланс:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        pause_exit()
        return 1

    username = f"@{me.username}" if getattr(me, "username", None) else f"{me.first_name} (без username)"
    print("\n✅ Сессия успешно создана и сохранена в папке sessions.")
    print(f"👤 Ник/аккаунт: {username} | ID: {me.id}")
    print(f"🌟 Кол-во звёзд: {bal}")

    await app.disconnect()
    pause_exit("\nНажмите Enter, чтобы закрыть...")
    return 0

if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        print("\nВыход (Ctrl+C).")
        pause_exit()
        code = 130
    except Exception as e:
        print("❌ ФАТАЛЬНАЯ ОШИБКА:", repr(e))
        traceback.print_exc()
        pause_exit()
        code = 1

    raise SystemExit(code)