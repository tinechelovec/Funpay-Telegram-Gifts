from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

HERE = Path(__file__).resolve().parent
GIFTS_JSON = HERE / "gifts.json"
SETS_JSON = HERE / "gift_sets.json"

CANCEL_TOKENS = {"0", "q", "й", "exit", "quit", "выход", "назад", "отмена", "cancel", "back"}

DEFAULT_GIFTS: Dict[str, Dict] = {
    "1":  {"id": 5170145012310081615, "title": "❤️ Сердце",     "price": 15},
    "2":  {"id": 5170233102089322756, "title": "🐻 Медведь",     "price": 15},
    "3":  {"id": 5170250947678437525, "title": "🎁 Подарок",     "price": 25},
    "4":  {"id": 5168103777563050263, "title": "🌹 Роза",        "price": 25},
    "5":  {"id": 5170144170496491616, "title": "🎂 Торт",        "price": 50},
    "6":  {"id": 5170314324215857265, "title": "💐 Цветы",       "price": 50},
    "7":  {"id": 5170564780938756245, "title": "🚀 Ракета",      "price": 50},
    "8":  {"id": 5168043875654172773, "title": "🏆 Кубок",       "price": 100},
    "9":  {"id": 5170690322832818290, "title": "💍 Кольцо",      "price": 100},
    "10": {"id": 5170521118301225164, "title": "💎 Алмаз",       "price": 100},
    "11": {"id": 6028601630662853006, "title": "🍾 Шампанское",  "price": 50},
}

@dataclass
class SetItem:
    gift_key: str
    qty: int = 1

@dataclass
class GiftSet:
    key: str
    title: str
    items: List[SetItem]

    def compute_price(self, base_gifts: Dict[str, Dict]) -> int:
        total = 0
        for it in self.items:
            if it.gift_key not in base_gifts:
                raise ValueError(
                    f"В наборе {self.key} обнаружен подарок {it.gift_key}, которого нет в gifts.json."
                )
            price = int(base_gifts[it.gift_key]["price"])
            total += price * int(it.qty)
        return total

    def expand_to_gift_ids(self, base_gifts: Dict[str, Dict]) -> List[int]:
        expanded: List[int] = []
        for it in self.items:
            gift = base_gifts[it.gift_key]
            expanded.extend([int(gift["id"])] * int(it.qty))
        return expanded

def load_base_gifts() -> Dict[str, Dict]:
    if GIFTS_JSON.exists():
        with GIFTS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): v for k, v in data.items()}
    return DEFAULT_GIFTS

def load_sets() -> Dict[str, GiftSet]:
    if SETS_JSON.exists():
        with SETS_JSON.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        out: Dict[str, GiftSet] = {}
        for k, v in raw.items():
            items = [
                SetItem(gift_key=str(i["gift_key"]), qty=int(i.get("qty", 1)))
                for i in v.get("items", [])
            ]
            out[str(k)] = GiftSet(key=str(k), title=v.get("title", f"Набор {k}"), items=items)
        return out
    return {}

def save_sets(sets: Dict[str, GiftSet]) -> None:
    payload = {}
    for k, s in sets.items():
        payload[k] = {"title": s.title, "items": [asdict(it) for it in s.items]}
    with SETS_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def resolve_to_gift_ids(key: str | int, base_gifts: Dict[str, Dict] | None = None) -> List[int]:
    base = base_gifts or load_base_gifts()
    sets = load_sets()
    key_s = str(key)
    if key_s in sets:
        return sets[key_s].expand_to_gift_ids(base)
    if key_s not in base:
        raise KeyError(f"Подарок {key} не найден.")
    return [int(base[key_s]["id"])]

def get_required_stars(key: str | int, base_gifts: Dict[str, Dict] | None = None) -> int:
    base = base_gifts or load_base_gifts()
    sets = load_sets()
    key_s = str(key)
    if key_s in sets:
        return sets[key_s].compute_price(base)
    if key_s not in base:
        raise KeyError(f"Подарок {key} не найден.")
    return int(base[key_s]["price"])

def _is_cancel_token(s: str) -> bool:
    return s.strip().lower() in CANCEL_TOKENS

def input_int(
    prompt: str,
    *,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    allow_blank: bool = False,
    allow_cancel: bool = True,
) -> Optional[int]:
    while True:
        s = input(prompt).strip()
        if allow_cancel and _is_cancel_token(s):
            return None
        if s == "" and allow_blank:
            return None
        if not s.lstrip("-").isdigit():
            print("Пожалуйста, введите число.")
            continue
        val = int(s)
        if min_val is not None and val < min_val:
            print(f"Число должно быть не меньше {min_val}.")
            continue
        if max_val is not None and val > max_val:
            print(f"Число должно быть не больше {max_val}.")
            continue
        return val

def input_str(prompt: str, *, allow_empty: bool = False, allow_cancel: bool = True) -> Optional[str]:
    while True:
        s = input(prompt).strip()
        if allow_cancel and _is_cancel_token(s):
            return None
        if s == "" and not allow_empty:
            print("Пустое значение недопустимо. Попробуйте ещё раз.")
            continue
        return s

def yes_no(prompt: str) -> bool:
    while True:
        s = input(f"{prompt} (y/n, 0 — отмена): ").strip().lower()
        if _is_cancel_token(s):
            return False
        if s in ("y", "yes", "д", "да"):
            return True
        if s in ("n", "no", "н", "нет"):
            return False
        print("Ответьте 'y' или 'n'.")

def press_enter():
    input("Нажмите Enter, чтобы продолжить...")

def print_gifts_catalog(base: Dict[str, Dict]):
    print("\nДоступные подарки (из gifts.json):")
    for k in sorted(base.keys(), key=lambda x: int(x)):
        g = base[k]
        print(f"  {k:>2}: {g['title']} — {g['price']}⭐ (Telegram ID: {g['id']})")
    print()

def summarize_set(s: GiftSet, base: Dict[str, Dict]) -> Tuple[str, int]:
    lines = [f"[{s.key}] {s.title}"]
    total = 0
    for it in s.items:
        g = base[it.gift_key]
        price = int(g["price"]) * it.qty
        total += price
        lines.append(f"  • {g['title']} — {it.qty} шт. = {price}⭐ (код {it.gift_key})")
    lines.append(f"Итого: {total}⭐")
    return "\n".join(lines), total

def choose_existing_set_id(sets: Dict[str, GiftSet]) -> Optional[str]:
    if not sets:
        print("Ещё нет созданных наборов.")
        return None
    print("\nТекущие наборы:")
    for k in sorted(sets.keys(), key=lambda x: int(x)):
        print(f"  {k}: {sets[k].title}")
    sid = input_int("\nУкажите ID набора (0 — назад): ", min_val=12, allow_cancel=True)
    if sid is None:
        return None
    sid_s = str(sid)
    if sid_s not in sets:
        print("Набор с таким ID не найден.")
        return None
    return sid_s

def cmd_create_set():
    base = load_base_gifts()
    sets = load_sets()

    print("\n=== Создание набора ===")
    print("Подсказка: для отмены введите 0, «отмена» или «назад».")
    print_gifts_catalog(base)

    title = input_str("Название набора (как увидит покупатель на FunPay): ", allow_empty=False, allow_cancel=True)
    if title is None:
        print("Создание набора отменено.")
        press_enter()
        return

    print("\nСоберите состав набора.")
    print("Вводите ID подарков по одному (1–11). Нажмите Enter, чтобы закончить.")
    gift_keys: List[str] = []
    i = 1
    while i <= 11:
        s = input(f"Подарок №{i} — ID (1–11, Enter — закончить, 0 — отмена): ").strip()
        if _is_cancel_token(s):
            print("Создание набора отменено.")
            press_enter()
            return
        if s == "":
            break
        if not s.isdigit() or s not in base:
            print("Укажите ID из списка (1–11).")
            continue
        gift_keys.append(s)
        i += 1

    if not gift_keys:
        print("Состав пустой — создание набора отменено.")
        press_enter()
        return

    print("\nУкажите количество для каждого подарка (в штуках).")
    items: List[SetItem] = []
    for idx, gk in enumerate(gift_keys, start=1):
        qty = input_int(
            f"{idx}. {base[gk]['title']} — сколько выдавать (1–999, 0 — отмена): ",
            min_val=1, max_val=999, allow_cancel=True
        )
        if qty is None:
            print("Создание набора отменено.")
            press_enter()
            return
        items.append(SetItem(gift_key=gk, qty=qty))

    while True:
        set_id = input_int(
            "\nУкажите ID набора (gift_tg для FunPay, число ≥ 12, 0 — отмена): ",
            min_val=12, max_val=10**9, allow_cancel=True
        )
        if set_id is None:
            print("Создание набора отменено.")
            press_enter()
            return
        set_id_s = str(set_id)
        if set_id_s in sets:
            print("Этот ID уже используется. Выберите другой.")
            continue
        break

    gift_set = GiftSet(key=set_id_s, title=title, items=items)

    summary, _total = summarize_set(gift_set, base)
    print("\nПроверите данные набора:")
    print(summary)
    if yes_no("Сохранить набор"):
        sets[set_id_s] = gift_set
        save_sets(sets)
        print("✅ Набор сохранён.")
    else:
        print("Сохранение отменено.")
    press_enter()

def cmd_edit_set():
    base = load_base_gifts()
    sets = load_sets()
    print("\n=== Редактирование набора ===")
    print("Подсказка: для отмены введите 0, «отмена» или «назад».")
    sid = choose_existing_set_id(sets)
    if not sid:
        press_enter()
        return
    s = sets[sid]

    print("\nЧто вы хотите изменить?")
    print("  1) Состав набора (пересобрать список подарков)")
    print("  2) Количество по позициям")
    print("  3) ID набора (gift_tg)")
    print("  4) Название набора")
    print("  0) Назад")

    choice = input_int("Ваш выбор: ", min_val=0, max_val=4, allow_cancel=True)
    if choice in (None, 0):
        press_enter()
        return

    changed = False

    if choice == 1:
        print_gifts_catalog(base)
        print("Вводите новые подарки по одному (1–11). Enter — закончить.")
        gift_keys: List[str] = []
        i = 1
        cancelled = False
        while i <= 11:
            val = input(f"Подарок №{i} — ID (Enter — закончить, 0 — отмена): ").strip()
            if _is_cancel_token(val):
                cancelled = True
                break
            if val == "":
                break
            if not val.isdigit() or val not in base:
                print("Укажите ID из списка (1–11).")
                continue
            gift_keys.append(val)
            i += 1
        if cancelled:
            print("Изменения отменены.")
        elif not gift_keys:
            print("Состав пустой — изменения не применены.")
        else:
            new_items: List[SetItem] = []
            for idx, gk in enumerate(gift_keys, start=1):
                qty = input_int(
                    f"{idx}. {base[gk]['title']} — новое количество (1–999, 0 — отмена): ",
                    min_val=1, max_val=999, allow_cancel=True
                )
                if qty is None:
                    print("Изменения отменены.")
                    press_enter()
                    return
                new_items.append(SetItem(gift_key=gk, qty=qty))
            s.items = new_items
            changed = True

    elif choice == 2:
        if not s.items:
            print("У набора нет позиций. Сначала задайте состав (п.1).")
        else:
            for it in s.items:
                qty = input_int(
                    f"{base[it.gift_key]['title']} — новое количество (сейчас {it.qty}, 1–999, 0 — отмена): ",
                    min_val=1, max_val=999, allow_cancel=True
                )
                if qty is None:
                    print("Изменения отменены.")
                    press_enter()
                    return
                it.qty = qty
            changed = True

    elif choice == 3:
        while True:
            new_id = input_int("Новый ID набора (число ≥ 12, 0 — отмена): ", min_val=12, allow_cancel=True)
            if new_id is None:
                print("Изменения отменены.")
                press_enter()
                return
            new_id_s = str(new_id)
            if new_id_s in sets and new_id_s != s.key:
                print("Этот ID уже используется. Выберите другой.")
                continue
            if new_id_s != s.key:
                sets.pop(s.key)
                s.key = new_id_s
                sets[new_id_s] = s
                changed = True
            break

    elif choice == 4:
        new_title = input_str("Новое название набора: ", allow_empty=False, allow_cancel=True)
        if new_title is None:
            print("Изменения отменены.")
            press_enter()
            return
        s.title = new_title
        changed = True

    if changed:
        summary, _total = summarize_set(s, base)
        print("\nОбновлённый набор:")
        print(summary)
        if yes_no("Сохранить изменения"):
            save_sets(sets)
            print("✅ Изменения сохранены.")
        else:
            print("Сохранение отменено.")
    else:
        print("Изменений нет.")
    press_enter()

def cmd_delete_set():
    sets = load_sets()
    print("\n=== Удаление набора ===")
    print("Подсказка: для отмены введите 0, «отмена» или «назад».")
    sid = choose_existing_set_id(sets)
    if not sid:
        press_enter()
        return

    if yes_no(f"Удалить набор {sid} безвозвратно"):
        sets.pop(sid, None)
        save_sets(sets)
        print("✅ Набор удалён.")
    else:
        print("Удаление отменено.")
    press_enter()

def cmd_list_sets():
    base = load_base_gifts()
    sets = load_sets()
    print("\n=== Список наборов ===")
    if not sets:
        print("Пока нет ни одного набора.")
    else:
        for k in sorted(sets.keys(), key=lambda x: int(x)):
            s = sets[k]
            summary, _total = summarize_set(s, base)
            print(summary)
            print(f"Использование в лоте FunPay: gift_tg: {k}")
            print("-" * 40)
    press_enter()

def main_menu():
    while True:
        print("\n==============================")
        print("          Главное меню        ")
        print("==============================")
        print("Подсказка: в любом месте можно ввести 0 / «отмена» / «назад».")
        print("\nДоступные действия:")
        print("  1. Создать набор")
        print("  2. Редактировать набор")
        print("  3. Удалить набор")
        print("  4. Посмотреть наборы")
        print("  0. Выход")
        print()

        choice = input_int("Выберите пункт (0–4): ", min_val=0, max_val=4, allow_cancel=True)
        if choice in (None, 0):
            print("Выход из программы.")
            break

        if choice == 1:
            cmd_create_set()
        elif choice == 2:
            cmd_edit_set()
        elif choice == 3:
            cmd_delete_set()
        elif choice == 4:
            cmd_list_sets()

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nВыход (Ctrl+C).")
