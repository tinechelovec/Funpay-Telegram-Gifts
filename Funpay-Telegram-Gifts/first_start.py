from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import stat
import sys
import traceback
from typing import Optional, Dict

from dotenv import load_dotenv, set_key
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, PhoneNumberUnoccupied

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
    "GIFT_PARAM_KEY": "gift_tg",
}

TG_FLOOD_DEFAULTS: Dict[str, str] = {
    "MIN_SEND_DELAY": "0.35",
    "PER_RECIPIENT_DELAY": "1.20",
    "SEND_JITTER": "0.08",
    "BURST_WINDOW_SECONDS": "10",
    "BURST_MAX_SENDS": "20",
    "USERNAME_CACHE_TTL": "86400",
    "FLOODWAIT_EXTRA_SLEEP": "0.30",
    "SPAMBLOCK_PAUSE_SECONDS": "21600",
    "AUTO_DEACTIVATE_ON_FLOODWAIT": "false",
    "FLOOD_DEACTIVATE_COOLDOWN": "900",
}

TG_FLOOD_HELP: Dict[str, str] = {
    "MIN_SEND_DELAY": "⏱️ Минимальная пауза между любыми запросами к Telegram (сек).",
    "PER_RECIPIENT_DELAY": "👤 Доп.пауза при отправках одному получателю (сек).",
    "SEND_JITTER": "🎲 Случайная добавка к задержкам (сек).",
    "BURST_WINDOW_SECONDS": "📦 Окно burst (сек): считаем отправки за этот интервал.",
    "BURST_MAX_SENDS": "🚦 Максимум отправок в burst-окне. Если превысили, будет ожидание.",
    "USERNAME_CACHE_TTL": "🧠 Кэш username→id (сек). Больше TTL = меньше resolve-запросов.",
    "FLOODWAIT_EXTRA_SLEEP": "🧯 Доп.ожидание поверх FloodWait (сек).",
    "SPAMBLOCK_PAUSE_SECONDS": "⛔ При PeerFlood/спам-блоке стоп на N секунд (21600 = 6 часов).",
    "AUTO_DEACTIVATE_ON_FLOODWAIT": "🔌 Если true, при flood можно выключать лоты (по CATEGORY_IDS).",
    "FLOOD_DEACTIVATE_COOLDOWN": "🕒 Деактивация при flood не чаще, чем раз в N секунд.",
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
    print("👋 Привет! Первичная настройка и Telegram-сессия (Pyrogram/pyrofork).")
    print("📌 Скрипт делает:")
    print("• Обновляет .env рядом со скриптом")
    print("• Создаёт Telegram-сессии и сохраняет в папку sessions")
    print("")
    print("✅ Обязательные пункты:")
    print("• FUNPAY_AUTH_TOKEN")
    print("• API_ID")
    print("• API_HASH")
    print("")
    print("ℹ️ Остальные пункты можно пропускать (Enter) и будут дефолты.")
    print("")

def _print_debug_info() -> None:
    print("────────────────────────────────────────")
    print("🧩 Debug")
    print("Python:", sys.version.replace("\n", " "))
    print("CWD:", os.getcwd())
    print("Script dir:", BASE_DIR)
    print("Env path:", ENV_PATH)
    print("Sessions dir:", WORKDIR)
    print("Sessions exists:", os.path.isdir(WORKDIR))
    try:
        mode = stat.S_IMODE(os.stat(WORKDIR).st_mode)
        print("Sessions perms:", oct(mode))
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
    p = argparse.ArgumentParser(description="Создание Pyrogram/pyrofork-сессий + заполнение .env")
    p.add_argument(
        "--set",
        dest="set_pairs",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Установить значение в .env без вопросов (можно несколько раз)",
    )
    p.add_argument(
        "--force-env",
        action="store_true",
        help="Спросить/перезаписать все поля .env (включая необязательные)",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Без вопросов: если обязательных ключей нет, ошибка (для хостинга)",
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
    if v in ("true", "t", "1", "yes", "y", "да", "д", "on"):
        return True
    if v in ("false", "f", "0", "no", "n", "нет", "н", "off"):
        return False
    raise ValueError("Ожидается true/false (или да/нет, 1/0).")

def _prompt_required_str(key: str, current: Optional[str], *, secret: bool = False) -> str:
    while True:
        if secret:
            hint = "задано" if current else "НЕ задано"
            print(f"\n🔐 {key} (секрет) сейчас: {hint}")
            val = getpass.getpass("Введите значение (Enter оставить, если уже задано): ").strip()
        else:
            cur_show = current if current else "(НЕ задано)"
            print(f"\n🧷 {key} сейчас: {cur_show}")
            val = input("Введите значение (Enter оставить, если уже задано): ").strip()
        if val == "":
            if current:
                return current
            print("❌ Это обязательный пункт. Пустым оставить нельзя.")
            continue
        return val

def _prompt_required_int(key: str, current: Optional[str]) -> int:
    while True:
        cur_show = current if current else "(НЕ задано)"
        print(f"\n🧷 {key} сейчас: {cur_show}")
        val = input("Введите число (Enter оставить, если уже задано): ").strip()
        if val == "":
            if current:
                try:
                    return int(current)
                except ValueError:
                    print("❌ В .env сейчас не число. Введите корректное значение.")
                    continue
            print("❌ Это обязательный пункт. Пустым оставить нельзя.")
            continue
        try:
            return int(val)
        except ValueError:
            print("❌ Нужно целое число.")

def _prompt_bool_key(key: str, current: Optional[str], default: bool) -> str:
    cur_show = current if current else "(не задано)"
    print(f"\n🧩 {key} сейчас: {cur_show}")
    print(f"Введите true/false (Enter дефолт: {str(default).lower()})")
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
    print(f"\n🗂️ {key} сейчас: {cur_show}")
    print(f"Введите через запятую (пример 3064,2418). Enter дефолт: {default}")
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
    print(f"\n🧮 {key} сейчас: {cur_show}")
    print(f"Введите число (Enter оставить/поставить: {cur_show})")
    while True:
        val = input("> ").strip()
        if val == "":
            return cur_show
        try:
            return str(float(val.replace(",", ".")))
        except ValueError:
            print("❌ Нужно число, например 1.0")

def _prompt_int_key(key: str, current: Optional[str], default: int, *, min_val: int = 0, max_val: int = 10**12) -> str:
    cur_show = current if current else str(default)
    print(f"\n🧮 {key} сейчас: {cur_show}")
    print(f"Введите целое число (Enter оставить/поставить: {cur_show})")
    while True:
        val = input("> ").strip()
        if val == "":
            return cur_show
        if not val.isdigit():
            print("❌ Нужно целое число.")
            continue
        n = int(val)
        if n < min_val:
            print(f"❌ Минимум: {min_val}")
            continue
        if n > max_val:
            print(f"❌ Максимум: {max_val}")
            continue
        return str(n)

def _prompt_gift_param_key(key: str, current: Optional[str], default: str) -> str:
    cur_show = current if current else "(не задано)"
    print(f"\n🏷️ {key} сейчас: {cur_show}")
    print("Это имя параметра в описании лота/заказа, чтобы бот понял какой подарок выдавать.")
    print("Пример: gift_tg:14 или gift_tg=14 или gift tg 14")
    print(f"Введите ключ (Enter дефолт: {default})")
    while True:
        val = input("> ").strip()
        if val == "":
            return current if current else default
        if len(val) > 64:
            print("❌ Слишком длинно. До 64 символов.")
            continue
        if not re.fullmatch(r"[A-Za-z0-9_\- ]{2,64}", val):
            print("❌ Допустимы: латиница, цифры, пробел, '_' и '-'. Пример: gift_tg")
            continue
        return val

def _ensure_optional_defaults_written() -> None:
    for k, v in DEFAULTS.items():
        if get_env(k) is None:
            set_key(ENV_PATH, k, v)
    for k, v in TG_FLOOD_DEFAULTS.items():
        if get_env(k) is None:
            set_key(ENV_PATH, k, v)
    load_dotenv(ENV_PATH, override=True)

def _ask_yes_no(prompt: str, *, default: Optional[bool] = None) -> bool:
    while True:
        if default is True:
            s = input(f"{prompt} (y/n, Enter=y): ").strip().lower()
        elif default is False:
            s = input(f"{prompt} (y/n, Enter=n): ").strip().lower()
        else:
            s = input(f"{prompt} (y/n): ").strip().lower()
        if s == "" and default is not None:
            return default
        if s in ("y", "yes", "д", "да"):
            return True
        if s in ("n", "no", "н", "нет"):
            return False
        print("❌ Ответьте y/n (да/нет).")

def tg_floodwait_setup(*, force_all: bool) -> Dict[str, str]:
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎁 Telegram-подарки: анти-FloodWait")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("Эти параметры снижают риск FloodWait/PeerFlood при отправке Stars-подарков.")
    print("Если часто ловите flood, увеличьте задержки и уменьшите burst.")
    print("")
    want = _ask_yes_no("⚙️ Хотите настроить параметры вручную?", default=False)
    updates: Dict[str, str] = {}
    if not want:
        print("\n✅ Ок, ставлю дефолты антифлуда:")
        for k, v in TG_FLOOD_DEFAULTS.items():
            updates[k] = v
            print(f"• {k} = {v}")
        return updates

    print("\n🛠️ Ручная настройка (Enter оставит текущее или дефолт).")

    def cur(k: str) -> Optional[str]:
        _ = force_all
        return get_env(k)

    float_keys = ("MIN_SEND_DELAY", "PER_RECIPIENT_DELAY", "SEND_JITTER", "BURST_WINDOW_SECONDS", "FLOODWAIT_EXTRA_SLEEP")
    int_keys = ("BURST_MAX_SENDS", "USERNAME_CACHE_TTL", "SPAMBLOCK_PAUSE_SECONDS", "FLOOD_DEACTIVATE_COOLDOWN")
    bool_keys = ("AUTO_DEACTIVATE_ON_FLOODWAIT",)

    for k in float_keys:
        print("\n" + "—" * 28)
        print(f"🔧 {k}")
        print(TG_FLOOD_HELP.get(k, ""))
        updates[k] = _prompt_float(k, cur(k), default=float(TG_FLOOD_DEFAULTS[k]))

    for k in int_keys:
        print("\n" + "—" * 28)
        print(f"🔧 {k}")
        print(TG_FLOOD_HELP.get(k, ""))
        updates[k] = _prompt_int_key(k, cur(k), default=int(TG_FLOOD_DEFAULTS[k]), min_val=0)

    for k in bool_keys:
        print("\n" + "—" * 28)
        print(f"🔧 {k}")
        print(TG_FLOOD_HELP.get(k, ""))
        updates[k] = _prompt_bool_key(k, cur(k), default=(TG_FLOOD_DEFAULTS[k] == "true"))

    print("\n✅ Готово.")
    return updates

def env_setup(*, force_all: bool, non_interactive: bool) -> None:
    if non_interactive:
        missing = [k for k in ("FUNPAY_AUTH_TOKEN", "API_ID", "API_HASH") if not get_env(k)]
        if missing:
            raise SystemExit(f"❌ Не хватает обязательных ключей в .env: {', '.join(missing)} (режим --non-interactive)")
        _ensure_optional_defaults_written()
        return

    print("🧩 Настройка .env")
    print("Обязательные пункты будут запрошены. Необязательные можно пропустить (Enter) и будут дефолты.\n")

    updates: Dict[str, str] = {}

    api_id = _prompt_required_int("API_ID", get_env("API_ID") if not force_all else None)
    updates["API_ID"] = str(api_id)

    api_hash = _prompt_required_str("API_HASH", get_env("API_HASH") if not force_all else None, secret=False)
    updates["API_HASH"] = api_hash

    funpay_token = _prompt_required_str("FUNPAY_AUTH_TOKEN", get_env("FUNPAY_AUTH_TOKEN") if not force_all else None, secret=True)
    updates["FUNPAY_AUTH_TOKEN"] = funpay_token

    if force_all or get_env("AUTO_REFUND") is None:
        updates["AUTO_REFUND"] = _prompt_bool_key("AUTO_REFUND", get_env("AUTO_REFUND"), default=(DEFAULTS["AUTO_REFUND"] == "true"))

    if force_all or get_env("AUTO_DEACTIVATE") is None:
        updates["AUTO_DEACTIVATE"] = _prompt_bool_key("AUTO_DEACTIVATE", get_env("AUTO_DEACTIVATE"), default=(DEFAULTS["AUTO_DEACTIVATE"] == "true"))

    if force_all or get_env("ANONYMOUS_GIFTS") is None:
        updates["ANONYMOUS_GIFTS"] = _prompt_bool_key("ANONYMOUS_GIFTS", get_env("ANONYMOUS_GIFTS"), default=(DEFAULTS["ANONYMOUS_GIFTS"] == "true"))

    if force_all or get_env("CATEGORY_IDS") is None:
        updates["CATEGORY_IDS"] = _prompt_category_ids("CATEGORY_IDS", get_env("CATEGORY_IDS"), default=DEFAULTS["CATEGORY_IDS"])

    if force_all or get_env("REPLY_COOLDOWN_SECONDS") is None:
        updates["REPLY_COOLDOWN_SECONDS"] = _prompt_float(
            "REPLY_COOLDOWN_SECONDS",
            get_env("REPLY_COOLDOWN_SECONDS"),
            default=float(DEFAULTS["REPLY_COOLDOWN_SECONDS"]),
        )

    if force_all or get_env("PRECHECK_BALANCE") is None:
        updates["PRECHECK_BALANCE"] = _prompt_bool_key("PRECHECK_BALANCE", get_env("PRECHECK_BALANCE"), default=(DEFAULTS["PRECHECK_BALANCE"] == "true"))

    if force_all or get_env("REQUIRE_PLUS_CONFIRMATION") is None:
        updates["REQUIRE_PLUS_CONFIRMATION"] = _prompt_bool_key(
            "REQUIRE_PLUS_CONFIRMATION",
            get_env("REQUIRE_PLUS_CONFIRMATION"),
            default=(DEFAULTS["REQUIRE_PLUS_CONFIRMATION"] == "true"),
        )

    if force_all or get_env("GIFT_PARAM_KEY") is None:
        updates["GIFT_PARAM_KEY"] = _prompt_gift_param_key("GIFT_PARAM_KEY", get_env("GIFT_PARAM_KEY"), default=DEFAULTS["GIFT_PARAM_KEY"])

    updates.update(tg_floodwait_setup(force_all=force_all))

    os.makedirs(BASE_DIR, exist_ok=True)
    for k, v in updates.items():
        set_key(ENV_PATH, k, v)

    load_dotenv(ENV_PATH, override=True)
    _ensure_optional_defaults_written()
    print("\n✅ .env обновлён.\n")

def ask_phone() -> str:
    print("📲 Создаём Telegram-сессию.")
    while True:
        phone = input("📱 Номер (пример +79991234567): ").strip().replace(" ", "")
        if not phone:
            print("❌ Номер не может быть пустым.")
            continue
        confirm = input(f"Вы ввели: {phone}. Верно? (да/нет): ").strip().lower()
        if confirm in ("да", "д", "y", "yes"):
            return phone
        print("Ок, введём заново.")

def ask_code() -> str:
    while True:
        code = input("🔐 Код из Telegram: ").strip().replace(" ", "")
        if code:
            return code
        print("❌ Код не может быть пустым.")

def _next_session_name(i: int) -> str:
    return "stars" if i == 1 else f"stars{i}"

async def _connect_and_show(app: Client) -> Optional[tuple]:
    try:
        await app.connect()
    except Exception:
        return None
    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
        username = f"@{me.username}" if getattr(me, "username", None) else f"{me.first_name} (без username)"
        return me, bal, username
    except Exception:
        return None

async def _ensure_one_session(api_id_int: int, api_hash: str, session_name: str) -> int:
    app = Client(session_name, api_id=api_id_int, api_hash=api_hash, workdir=WORKDIR)
    res = await _connect_and_show(app)
    if res is not None:
        me, bal, username = res
        print("✅ Сессия уже существует (вход выполнен).")
        print(f"🧾 Файл: {session_name}.session")
        print(f"👤 Аккаунт: {username} | ID: {me.id}")
        print(f"🌟 Stars: {bal}")
        try:
            await app.disconnect()
        except Exception:
            pass
        return 0

    try:
        await app.disconnect()
    except Exception:
        pass

    app = Client(session_name, api_id=api_id_int, api_hash=api_hash, workdir=WORKDIR)
    try:
        await app.connect()
    except Exception as e:
        print("❌ Не удалось подключиться:", repr(e))
        traceback.print_exc()
        return 1

    phone = ask_phone()
    try:
        sent = await app.send_code(phone)
    except PhoneNumberInvalid:
        print("❌ Неверный формат номера.")
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
        await app.sign_in(phone_number=phone, phone_code_hash=sent.phone_code_hash, phone_code=code)
    except PhoneCodeInvalid:
        print("❌ Код неверный.")
        await app.disconnect()
        return 1
    except PhoneCodeExpired:
        print("❌ Код устарел.")
        await app.disconnect()
        return 1
    except SessionPasswordNeeded:
        pwd = getpass.getpass("🔒 2FA включена. Пароль: ").strip()
        try:
            await app.check_password(pwd)
        except Exception as e:
            print("❌ Ошибка 2FA пароля:", repr(e))
            traceback.print_exc()
            await app.disconnect()
            return 1
    except Exception as e:
        print("❌ Ошибка при sign_in:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        return 1

    try:
        me = await app.get_me()
        bal = await app.get_stars_balance()
    except Exception as e:
        print("⚠️ Вход прошёл, но не удалось получить профиль/баланс:", repr(e))
        traceback.print_exc()
        await app.disconnect()
        return 1

    username = f"@{me.username}" if getattr(me, "username", None) else f"{me.first_name} (без username)"
    print("\n✅ Сессия создана и сохранена в папке sessions.")
    print(f"🧾 Файл: {session_name}.session")
    print(f"👤 Аккаунт: {username} | ID: {me.id}")
    print(f"🌟 Stars: {bal}")

    try:
        await app.disconnect()
    except Exception:
        pass
    return 0

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

    idx = 1
    while True:
        session_name = _next_session_name(idx)
        print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📲 Telegram-аккаунт #{idx}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        rc = await _ensure_one_session(api_id_int, API_HASH, session_name)
        if rc != 0:
            return rc
        if not _ask_yes_no("\n➕ Добавить ещё один аккаунт?", default=False):
            break
        idx += 1

    print("\n✅ Готово. Сессии лежат в папке sessions.")
    return 0

if __name__ == "__main__":
    args = build_args()
    try:
        exit_code = asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nВыход (Ctrl+C).")
        exit_code = 130
    except Exception as e:
        print("❌ Фатальная ошибка:", repr(e))
        traceback.print_exc()
        exit_code = 1

    pause_exit("\nНажмите Enter, чтобы закрыть...")
    raise SystemExit(exit_code)
