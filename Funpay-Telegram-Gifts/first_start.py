import asyncio
import os
import sys
import stat
import getpass
import traceback
import argparse
from typing import Optional, Dict

from dotenv import load_dotenv, set_key

from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PhoneNumberUnoccupied,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
WORKDIR = os.path.join(BASE_DIR, "sessions")

load_dotenv(ENV_PATH, override=True)

if not hasattr(Client, "send_gift"):
    raise RuntimeError(
        "Установлен неподдерживаемый пакет 'pyrogram'. Нужен форк с поддержкой Stars.\n"
        "Используйте: pip uninstall -y pyrogram && pip install -U pyrofork tgcrypto"
    )

DEFAULTS: Dict[str, str] = {
    "AUTO_REFUND": "true",
    "AUTO_DEACTIVATE": "true",
    "ANONYMOUS_GIFTS": "true",
    "CATEGORY_IDS": "3064,2418",
    "REPLY_COOLDOWN_SECONDS": "1.0",
    "PRECHECK_BALANCE": "true",
    "REQUIRE_PLUS_CONFIRMATION": "false",
}


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


def _print_intro() -> None:
    print("👋 Привет! Это скрипт для первичной настройки и создания Telegram-сессии (Pyrogram/pyrofork).")
    print("Он делает 2 вещи:")
    print("  1) Заполняет/обновляет файл .env рядом со скриптом")
    print("  2) Создаёт или проверяет Telegram-сессию и сохраняет её в папку sessions\n")
    print("✅ Обязательные пункты (их нельзя пропустить):")
    print("  - FUNPAY_AUTH_TOKEN")
    print("  - API_ID")
    print("  - API_HASH\n")
    print("ℹ️ Остальные пункты можно пропустить (Enter) — тогда поставятся значения по умолчанию.\n")


def _print_debug_info() -> None:
    print("────────────────────────────────────────")
    print("Debug info:")
    print("Python:", sys.version.replace("\n", " "))
    print("CWD:", os.getcwd())
    print("Script dir:", BASE_DIR)
    print("Env path:", ENV_PATH)
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


def get_env(key: str) -> Optional[str]:
    v = os.getenv(key)
    if v is None:
        return None
    v = v.strip()
    return v if v else None


def build_args():
    p = argparse.ArgumentParser(
        description="Создание Pyrogram/pyrofork-сессии + обязательное заполнение .env"
    )
    p.add_argument(
        "--set",
        dest="set_pairs",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Установить значение в .env без вопросов (можно указывать несколько раз)",
    )
    p.add_argument(
        "--force-env",
        action="store_true",
        help="Спросить/перезаписать все поля .env (включая необязательные)",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Без вопросов: если обязательных ключей нет — ошибка (для хостинга)",
    )
    return p.parse_args()


def _apply_set_pairs(pairs: list[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"--set ожидает формат KEY=VALUE, получено: {item}")
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise ValueError(f"Пустой KEY в --set: {item}")
        out[k] = v
    return out


def _parse_bool(s: str) -> bool:
    v = s.strip().lower()
    if v in ("true", "t", "1", "yes", "y", "да", "д"):
        return True
    if v in ("false", "f", "0", "no", "n", "нет", "н"):
        return False
    raise ValueError("Ожидается true/false (или да/нет, 1/0).")


def _prompt_required_str(key: str, current: Optional[str], *, secret: bool = False) -> str:
    while True:
        if secret:
            hint = "задано" if current else "НЕ задано"
            print(f"\n{key} (секрет) — сейчас: {hint}")
            val = getpass.getpass("Введите значение (Enter — оставить как есть, если уже задано): ").strip()
        else:
            cur_show = current if current else "(НЕ задано)"
            print(f"\n{key} — сейчас: {cur_show}")
            val = input("Введите значение (Enter — оставить как есть, если уже задано): ").strip()

        if val == "":
            if current:
                return current
            print("❌ Это обязательный пункт. Пустым оставить нельзя.")
            continue

        return val


def _prompt_required_int(key: str, current: Optional[str]) -> int:
    while True:
        cur_show = current if current else "(НЕ задано)"
        print(f"\n{key} — сейчас: {cur_show}")
        val = input("Введите число (Enter — оставить как есть, если уже задано): ").strip()

        if val == "":
            if current:
                try:
                    return int(current)
                except ValueError:
                    print("❌ В .env сейчас не число, нужно ввести корректный API_ID.")
                    continue
            print("❌ Это обязательный пункт. Пустым оставить нельзя.")
            continue

        try:
            return int(val)
        except ValueError:
            print("❌ Нужно целое число.")


def _prompt_bool_key(key: str, current: Optional[str], default: bool) -> str:
    cur_show = current if current else "(не задано)"
    print(f"\n{key} — сейчас: {cur_show}")
    print(f"Введите true/false (Enter — поставить дефолт: {str(default).lower()})")
    while True:
        val = input("> ").strip()
        if val == "":
            return str(default).lower()
        try:
            return str(_parse_bool(val)).lower()
        except ValueError as e:
            print(f"❌ {e}")


def _prompt_category_ids(key: str, current: Optional[str], default: str) -> str:
    cur_show = current if current else "(не задано)"
    print(f"\n{key} — сейчас: {cur_show}")
    print(f"Введите через запятую, например: 3064,2418 (Enter — дефолт: {default})")
    while True:
        val = input("> ").strip().replace(" ", "")
        if val == "":
            return current if current else default

        parts = [p for p in val.split(",") if p]
        if not parts:
            print("❌ Пусто. Пример: 3064,2418")
            continue
        if any(not p.isdigit() for p in parts):
            print("❌ Все значения должны быть числами. Пример: 3064,2418")
            continue
        return ",".join(parts)


def _prompt_float(key: str, current: Optional[str], default: float) -> str:
    cur_show = current if current else str(default)
    print(f"\n{key} — сейчас: {cur_show}")
    print(f"Введите число (Enter — поставить/оставить: {cur_show})")
    while True:
        val = input("> ").strip()
        if val == "":
            return cur_show
        try:
            return str(float(val.replace(",", ".")))
        except ValueError:
            print("❌ Нужно число, например 1.0")


def _ensure_optional_defaults_written() -> None:
    for k, v in DEFAULTS.items():
        if get_env(k) is None:
            set_key(ENV_PATH, k, v)
    load_dotenv(ENV_PATH, override=True)


def env_setup(*, force_all: bool, non_interactive: bool) -> None:
    if non_interactive:
        missing = [k for k in ("FUNPAY_AUTH_TOKEN", "API_ID", "API_HASH") if not get_env(k)]
        if missing:
            raise SystemExit(
                f"❌ Не хватает обязательных ключей в .env: {', '.join(missing)} (режим --non-interactive)"
            )
        _ensure_optional_defaults_written()
        return

    print("🧩 Настройка .env")
    print("Обязательные пункты будут запрошены. Необязательные можно пропустить (Enter) — будут дефолты.\n")

    updates: Dict[str, str] = {}

    api_id = _prompt_required_int("API_ID", get_env("API_ID") if not force_all else None)
    updates["API_ID"] = str(api_id)

    api_hash = _prompt_required_str("API_HASH", get_env("API_HASH") if not force_all else None, secret=False)
    updates["API_HASH"] = api_hash

    funpay_token = _prompt_required_str(
        "FUNPAY_AUTH_TOKEN",
        get_env("FUNPAY_AUTH_TOKEN") if not force_all else None,
        secret=True
    )
    updates["FUNPAY_AUTH_TOKEN"] = funpay_token

    if force_all or get_env("AUTO_REFUND") is None:
        updates["AUTO_REFUND"] = _prompt_bool_key(
            "AUTO_REFUND", get_env("AUTO_REFUND"), default=(DEFAULTS["AUTO_REFUND"] == "true")
        )

    if force_all or get_env("AUTO_DEACTIVATE") is None:
        updates["AUTO_DEACTIVATE"] = _prompt_bool_key(
            "AUTO_DEACTIVATE", get_env("AUTO_DEACTIVATE"), default=(DEFAULTS["AUTO_DEACTIVATE"] == "true")
        )

    if force_all or get_env("ANONYMOUS_GIFTS") is None:
        updates["ANONYMOUS_GIFTS"] = _prompt_bool_key(
            "ANONYMOUS_GIFTS", get_env("ANONYMOUS_GIFTS"), default=(DEFAULTS["ANONYMOUS_GIFTS"] == "true")
        )

    if force_all or get_env("CATEGORY_IDS") is None:
        updates["CATEGORY_IDS"] = _prompt_category_ids(
            "CATEGORY_IDS", get_env("CATEGORY_IDS"), default=DEFAULTS["CATEGORY_IDS"]
        )

    if force_all or get_env("REPLY_COOLDOWN_SECONDS") is None:
        updates["REPLY_COOLDOWN_SECONDS"] = _prompt_float(
            "REPLY_COOLDOWN_SECONDS",
            get_env("REPLY_COOLDOWN_SECONDS"),
            default=float(DEFAULTS["REPLY_COOLDOWN_SECONDS"])
        )

    if force_all or get_env("PRECHECK_BALANCE") is None:
        updates["PRECHECK_BALANCE"] = _prompt_bool_key(
            "PRECHECK_BALANCE", get_env("PRECHECK_BALANCE"), default=(DEFAULTS["PRECHECK_BALANCE"] == "true")
        )

    if force_all or get_env("REQUIRE_PLUS_CONFIRMATION") is None:
        updates["REQUIRE_PLUS_CONFIRMATION"] = _prompt_bool_key(
            "REQUIRE_PLUS_CONFIRMATION",
            get_env("REQUIRE_PLUS_CONFIRMATION"),
            default=(DEFAULTS["REQUIRE_PLUS_CONFIRMATION"] == "true")
        )

    os.makedirs(BASE_DIR, exist_ok=True)
    for k, v in updates.items():
        set_key(ENV_PATH, k, v)

    load_dotenv(ENV_PATH, override=True)
    _ensure_optional_defaults_written()

    print("\n✅ .env обновлён.\n")


def ask_phone() -> str:
    print("📲 Дальше нужно создать Telegram-сессию.")
    print("Введите номер телефона Telegram.\n")

    while True:
        phone = input("📱 Введите номер телефона (пример: +79991234567): ").strip().replace(" ", "")
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


async def main(args) -> int:
    _print_intro()
    _ensure_sessions_dir()
    _print_debug_info()

    if args.set_pairs:
        try:
            updates = _apply_set_pairs(args.set_pairs)
        except ValueError as e:
            print("❌", e)
            return 1

        os.makedirs(BASE_DIR, exist_ok=True)
        for k, v in updates.items():
            set_key(ENV_PATH, k, v)
        load_dotenv(ENV_PATH, override=True)
        print("✅ Применены значения из --set в .env\n")

    env_setup(force_all=args.force_env, non_interactive=args.non_interactive)

    API_ID = get_env("API_ID")
    API_HASH = get_env("API_HASH")
    FUNPAY_AUTH_TOKEN = get_env("FUNPAY_AUTH_TOKEN")

    missing = [k for k, v in (("API_ID", API_ID), ("API_HASH", API_HASH), ("FUNPAY_AUTH_TOKEN", FUNPAY_AUTH_TOKEN)) if not v]
    if missing:
        print("❌ Не хватает обязательных ключей в .env:", ", ".join(missing))
        return 1

    try:
        api_id_int = int(API_ID)
    except ValueError:
        print("❌ API_ID должен быть числом.")
        return 1

    app = Client("stars", api_id=api_id_int, api_hash=API_HASH, workdir=WORKDIR)

    try:
        await app.connect()
    except Exception as e:
        print("❌ Не удалось подключиться (app.connect):", repr(e))
        traceback.print_exc()
        return 1

    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
        username = f"@{me.username}" if getattr(me, "username", None) else f"{me.first_name} (без username)"
        print("✅ Сессия уже существует (вход выполнен).")
        print(f"👤 Аккаунт: {username} | ID: {me.id}")
        print(f"🌟 Кол-во звёзд: {bal}")
        await app.disconnect()
        return 0
    except Exception:
        pass

    phone = ask_phone()

    try:
        sent = await app.send_code(phone)
    except PhoneNumberInvalid:
        print("❌ Неверный формат номера телефона. Перезапустите и введите номер правильно.")
        await app.disconnect()
        return 1
    except PhoneNumberUnoccupied:
        print("❌ Этот номер не зарегистрирован в Telegram.")
        await app.disconnect()
        return 1
    except Exception as e:
        print("❌ Ошибка при отправке кода:", repr(e))
        traceback.print_exc()
        await app.disconnect()
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
        return 1
    except PhoneCodeExpired:
        print("❌ Код устарел. Перезапустите скрипт и запросите новый код.")
        await app.disconnect()
        return 1
    except SessionPasswordNeeded:
        pwd = getpass.getpass("🔒 Включена двухэтапная проверка (2FA). Введите пароль: ")
        try:
            await app.check_password(pwd)
        except Exception as e:
            print("❌ Ошибка 2FA пароля:", repr(e))
            traceback.print_exc()
            await app.disconnect()
            return 1
    except Exception as e:
        print("❌ НЕОЖИДАННАЯ ОШИБКА при sign_in:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        return 1

    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
    except Exception as e:
        print("⚠️ Вход вроде прошёл, но не удалось получить профиль/баланс:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        return 1

    username = f"@{me.username}" if getattr(me, "username", None) else f"{me.first_name} (без username)"
    print("\n✅ Сессия успешно создана и сохранена в папке sessions.")
    print(f"👤 Ник/аккаунт: {username} | ID: {me.id}")
    print(f"🌟 Кол-во звёзд: {bal}")

    await app.disconnect()
    return 0


if __name__ == "__main__":
    args = build_args()
    try:
        exit_code = asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nВыход (Ctrl+C).")
        exit_code = 130
    except Exception as e:
        print("❌ ФАТАЛЬНАЯ ОШИБКА:", repr(e))
        traceback.print_exc()
        exit_code = 1

    pause_exit("\nНажмите Enter, чтобы закрыть...")
    raise SystemExit(exit_code)
