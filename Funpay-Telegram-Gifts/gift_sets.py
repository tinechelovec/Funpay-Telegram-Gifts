from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union

HERE = Path(__file__).resolve().parent
GIFTS_JSON = HERE / "gifts.json"
SETS_JSON = HERE / "gift_sets.json"

CANCEL_TOKENS = {"0", "q", "й", "exit", "quit", "выход", "назад", "отмена", "cancel", "back"}

DEFAULT_GIFTS: Dict[str, Dict] = {
    "1": {"id": 5170145012310081615, "title": "❤️ Сердце", "price": 15},
    "2": {"id": 5170233102089322756, "title": "🐻 Медведь", "price": 15},
    "3": {"id": 5170250947678437525, "title": "🎁 Подарок", "price": 25},
    "4": {"id": 5168103777563050263, "title": "🌹 Роза", "price": 25},
    "5": {"id": 5170144170496491616, "title": "🎂 Торт", "price": 50},
    "6": {"id": 5170314324215857265, "title": "💐 Цветы", "price": 50},
    "7": {"id": 5170564780938756245, "title": "🚀 Ракета", "price": 50},
    "8": {"id": 5168043875654172773, "title": "🏆 Кубок", "price": 100},
    "9": {"id": 5170690322832818290, "title": "💍 Кольцо", "price": 100},
    "10": {"id": 5170521118301225164, "title": "💎 Алмаз", "price": 100},
    "11": {"id": 6028601630662853006, "title": "🍾 Шампанское", "price": 50},
    "12": {"id": 5922558454332916696, "title": "🎄 Ёлка", "price": 50},
    "13": {"id": 5956217000635139069, "title": "🐻 Новогодний медведь", "price": 50},
}

MAX_SET_SLOTS = 50

@dataclass
class SetItem:
    gift_key: str
    qty: int = 1

@dataclass
class FixedGiftSet:
    key: str
    title: str
    items: List[SetItem] = field(default_factory=list)
    mode: str = "fixed"

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

@dataclass
class ChoiceGiftSet:
    key: str
    title: str
    options: List[str] = field(default_factory=list)
    mode: str = "choice"

    def inferred_price(self, base_gifts: Dict[str, Dict]) -> Optional[int]:
        prices: List[int] = []
        for gk in self.options:
            g = base_gifts.get(str(gk))
            if not g:
                continue
            prices.append(int(g.get("price", 0) or 0))
        prices = [p for p in prices if p > 0]
        if not prices:
            return None
        if all(p == prices[0] for p in prices):
            return prices[0]
        return None

    def compute_price(self, base_gifts: Dict[str, Dict]) -> int:
        p = self.inferred_price(base_gifts)
        if p is None:
            raise ValueError("choice_set_price_unknown")
        return int(p)

GiftSet = Union[FixedGiftSet, ChoiceGiftSet]

def load_base_gifts() -> Dict[str, Dict]:
    if GIFTS_JSON.exists():
        with GIFTS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): v for k, v in data.items()}
    return DEFAULT_GIFTS

def load_sets() -> Dict[str, GiftSet]:
    if not SETS_JSON.exists():
        return {}
    with SETS_JSON.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    out: Dict[str, GiftSet] = {}
    for k, v in raw.items():
        k = str(k)
        mode = str(v.get("mode", "fixed")).lower().strip()

        if mode == "choice":
            options_raw = v.get("options", []) or []
            options: List[str] = []
            for it in options_raw:
                if isinstance(it, str):
                    options.append(it)
                elif isinstance(it, dict) and "gift_key" in it:
                    options.append(str(it["gift_key"]))

            out[k] = ChoiceGiftSet(
                key=k,
                title=v.get("title", f"Набор {k}"),
                options=[str(x) for x in options],
            )
        else:
            items = [
                SetItem(gift_key=str(i["gift_key"]), qty=int(i.get("qty", 1)))
                for i in v.get("items", []) or []
                if isinstance(i, dict) and "gift_key" in i
            ]
            out[k] = FixedGiftSet(key=k, title=v.get("title", f"Набор {k}"), items=items)

    return out

def save_sets(sets: Dict[str, GiftSet]) -> None:
    payload: Dict[str, dict] = {}
    for k, s in sets.items():
        if isinstance(s, ChoiceGiftSet):
            payload[k] = {
                "mode": "choice",
                "title": s.title,
                "options": [str(x) for x in s.options],
            }
        else:
            payload[k] = {
                "mode": "fixed",
                "title": s.title,
                "items": [asdict(it) for it in s.items],
            }

    with SETS_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def resolve_to_gift_ids(key: str | int, base_gifts: Dict[str, Dict] | None = None) -> List[int]:
    base = base_gifts or load_base_gifts()
    sets = load_sets()
    key_s = str(key)

    if key_s in sets:
        s = sets[key_s]
        if isinstance(s, ChoiceGiftSet):
            raise ValueError("Это набор-выбор. Нужен выбор конкретного подарка.")
        return s.expand_to_gift_ids(base)

    if key_s not in base:
        raise KeyError(f"Подарок {key} не найден.")
    return [int(base[key_s]["id"])]

def get_required_stars(key: str | int, base_gifts: Dict[str, Dict] | None = None) -> int:
    base = base_gifts or load_base_gifts()
    sets = load_sets()
    key_s = str(key)

    if key_s in sets:
        s = sets[key_s]
        if isinstance(s, ChoiceGiftSet):
            return s.compute_price(base)
        return int(s.compute_price(base))

    if key_s not in base:
        raise KeyError(f"Подарок {key} не найден.")
    return int(base[key_s]["price"])

def get_gift_id_bounds(base: Dict[str, Dict]) -> Tuple[int, int]:
    ids: List[int] = []
    for k in base.keys():
        try:
            ids.append(int(k))
        except ValueError:
            pass
    if not ids:
        return 1, 1
    return min(ids), max(ids)

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
    print("\n🎁 Доступные подарки (из gifts.json):")
    for k in sorted(base.keys(), key=lambda x: int(x) if x.isdigit() else 10**18):
        g = base[k]
        print(f"  {k:>2}: {g['title']} — {g['price']}⭐ (Telegram ID: {g['id']})")
    print()

def summarize_fixed_set(s: FixedGiftSet, base: Dict[str, Dict]) -> Tuple[str, int]:
    lines = [f"🧩 [{s.key}] {s.title}  (Обычный набор)"]
    total = 0
    for it in s.items:
        g = base[it.gift_key]
        price = int(g["price"]) * it.qty
        total += price
        lines.append(f"  • {g['title']} — {it.qty} шт. = {price}⭐ (код {it.gift_key})")
    lines.append(f"💰 Итого: {total}⭐")
    return "\n".join(lines), total

def summarize_choice_set(s: ChoiceGiftSet, base: Dict[str, Dict]) -> Tuple[str, Optional[int]]:
    lines = [f"🎲 [{s.key}] {s.title}  (Набор-выбор)"]
    lines.append("📝 Цена задаётся продавцом на FunPay (в лоте).")
    p = s.inferred_price(base)
    if p is not None:
        lines.append(f"ℹ️ Подсказка: все варианты по {p}⭐.")
    else:
        lines.append("⚠️ Варианты разной цены (проверьте, что цена лота на FunPay вам подходит).")

    lines.append("🎁 Варианты:")
    for gk in s.options:
        g = base.get(str(gk))
        if g:
            lines.append(f"  • {gk}: {g['title']} ({g.get('price', '?')}⭐)")
        else:
            lines.append(f"  • {gk}: (нет в gifts.json)")
    return "\n".join(lines), p

def choose_existing_set_id(sets: Dict[str, GiftSet], *, min_set_id: int) -> Optional[str]:
    if not sets:
        print("Пока нет созданных наборов.")
        return None

    print("\n📦 Текущие наборы:")
    for k in sorted(sets.keys(), key=lambda x: int(x) if x.isdigit() else 10**18):
        s = sets[k]
        tag = "🎲 выбор" if isinstance(s, ChoiceGiftSet) else "🧩 обычный"
        print(f"  {k}: {s.title} ({tag})")

    sid = input_int(f"\nУкажите ID набора (число ≥ {min_set_id}, 0 — назад): ", min_val=min_set_id, allow_cancel=True)
    if sid is None:
        return None
    sid_s = str(sid)
    if sid_s not in sets:
        print("Набор с таким ID не найден.")
        return None
    return sid_s

def _calc_min_set_id(base: Dict[str, Dict]) -> int:
    _gift_min, gift_max = get_gift_id_bounds(base)
    return gift_max + 1

def cmd_create_fixed_set():
    base = load_base_gifts()
    sets = load_sets()

    gift_min, gift_max = get_gift_id_bounds(base)
    min_set_id = _calc_min_set_id(base)

    print("\n=== 🧩 Создание обычного набора ===")
    print("Отмена: 0 / «отмена» / «назад».")
    print_gifts_catalog(base)

    title = input_str("Название набора: ", allow_empty=False, allow_cancel=True)
    if title is None:
        print("Отменено.")
        press_enter()
        return

    print("\nСоберите состав набора.")
    print(f"Введите ID подарков по одному ({gift_min}–{gift_max}). Enter — закончить.")
    gift_keys: List[str] = []
    i = 1
    while i <= MAX_SET_SLOTS:
        s = input(f"Подарок №{i} — ID: ").strip()
        if _is_cancel_token(s):
            print("Отменено.")
            press_enter()
            return
        if s == "":
            break
        if not s.isdigit() or s not in base:
            print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
            continue
        gift_keys.append(s)
        i += 1

    if not gift_keys:
        print("Состав пустой — отменено.")
        press_enter()
        return

    print("\nКоличество для каждого подарка:")
    items: List[SetItem] = []
    for idx, gk in enumerate(gift_keys, start=1):
        qty = input_int(
            f"{idx}. {base[gk]['title']} — сколько выдавать (1–999): ",
            min_val=1, max_val=999, allow_cancel=True,
        )
        if qty is None:
            print("Отменено.")
            press_enter()
            return
        items.append(SetItem(gift_key=gk, qty=qty))

    while True:
        set_id = input_int(
            f"\nID набора (gift_tg), число ≥ {min_set_id}: ",
            min_val=min_set_id, max_val=10**9, allow_cancel=True,
        )
        if set_id is None:
            print("Отменено.")
            press_enter()
            return
        set_id_s = str(set_id)
        if set_id_s in sets:
            print("Этот ID уже используется. Выберите другой.")
            continue
        break

    s = FixedGiftSet(key=set_id_s, title=title, items=items)

    summary, _ = summarize_fixed_set(s, base)
    print("\nПроверьте:")
    print(summary)
    if yes_no("Сохранить"):
        sets[set_id_s] = s
        save_sets(sets)
        print("✅ Сохранено.")
    else:
        print("Не сохранено.")
    press_enter()

def cmd_create_choice_set():
    base = load_base_gifts()
    sets = load_sets()

    gift_min, gift_max = get_gift_id_bounds(base)
    min_set_id = _calc_min_set_id(base)

    print("\n=== 🎲 Создание набора-выбора ===")
    print("Идея: покупатель покупает лот на FunPay и выбирает 1 подарок из списка.")
    print("Цена выставляется ВАМИ на FunPay (здесь не задаём).")
    print("Отмена: 0 / «отмена» / «назад».")
    print_gifts_catalog(base)

    title = input_str("Название набора-выбора: ", allow_empty=False, allow_cancel=True)
    if title is None:
        print("Отменено.")
        press_enter()
        return

    print("\nДобавьте варианты подарков (что можно выбрать).")
    print(f"Введите ID подарка ({gift_min}–{gift_max}). Enter — закончить.")
    options: List[str] = []
    i = 1
    while i <= MAX_SET_SLOTS:
        s = input(f"Вариант №{i} — ID: ").strip()
        if _is_cancel_token(s):
            print("Отменено.")
            press_enter()
            return
        if s == "":
            break
        if not s.isdigit() or s not in base:
            print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
            continue
        if s in options:
            print("Уже добавлено.")
            continue
        options.append(s)
        i += 1

    if not options:
        print("Список вариантов пустой — отменено.")
        press_enter()
        return

    while True:
        set_id = input_int(
            f"\nID набора (gift_tg), число ≥ {min_set_id}: ",
            min_val=min_set_id, max_val=10**9, allow_cancel=True,
        )
        if set_id is None:
            print("Отменено.")
            press_enter()
            return
        set_id_s = str(set_id)
        if set_id_s in sets:
            print("Этот ID уже используется. Выберите другой.")
            continue
        break

    s = ChoiceGiftSet(key=set_id_s, title=title, options=options)

    summary, _maybe_price = summarize_choice_set(s, base)
    print("\nПроверьте:")
    print(summary)
    if yes_no("Сохранить"):
        sets[set_id_s] = s
        save_sets(sets)
        print("✅ Сохранено.")
    else:
        print("Не сохранено.")
    press_enter()


def cmd_edit_set():
    base = load_base_gifts()
    sets = load_sets()
    min_set_id = _calc_min_set_id(base)
    gift_min, gift_max = get_gift_id_bounds(base)

    print("\n=== ✏️ Редактирование набора ===")
    sid = choose_existing_set_id(sets, min_set_id=min_set_id)
    if not sid:
        press_enter()
        return

    s = sets[sid]
    changed = False

    if isinstance(s, ChoiceGiftSet):
        print("\nЧто изменить?")
        print("  1) Список вариантов")
        print("  2) ID набора (gift_tg)")
        print("  3) Название")
        print("  0) Назад")
        choice = input_int("Ваш выбор: ", min_val=0, max_val=3, allow_cancel=True)
        if choice in (None, 0):
            press_enter()
            return

        if choice == 1:
            print_gifts_catalog(base)
            print(f"Новые варианты ({gift_min}–{gift_max}). Enter — закончить.")
            opts: List[str] = []
            i = 1
            while i <= MAX_SET_SLOTS:
                val = input(f"Вариант №{i} — ID: ").strip()
                if _is_cancel_token(val):
                    print("Отменено.")
                    press_enter()
                    return
                if val == "":
                    break
                if not val.isdigit() or val not in base:
                    print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
                    continue
                if val in opts:
                    print("Уже добавлено.")
                    continue
                opts.append(val)
                i += 1
            if not opts:
                print("Пусто — изменения не применены.")
            else:
                s.options = opts
                changed = True

        elif choice == 2:
            while True:
                new_id = input_int(f"Новый ID (число ≥ {min_set_id}): ", min_val=min_set_id, allow_cancel=True)
                if new_id is None:
                    print("Отменено.")
                    press_enter()
                    return
                new_id_s = str(new_id)
                if new_id_s in sets and new_id_s != s.key:
                    print("Этот ID уже используется.")
                    continue
                if new_id_s != s.key:
                    sets.pop(s.key)
                    s.key = new_id_s
                    sets[new_id_s] = s
                    changed = True
                break

        elif choice == 3:
            new_title = input_str("Новое название: ", allow_empty=False, allow_cancel=True)
            if new_title is None:
                print("Отменено.")
                press_enter()
                return
            s.title = new_title
            changed = True

        if changed:
            summary, _ = summarize_choice_set(s, base)
            print("\nОбновлено:")
            print(summary)
            if yes_no("Сохранить"):
                save_sets(sets)
                print("✅ Изменения сохранены.")
            else:
                print("Не сохранено.")
        else:
            print("Изменений нет.")
        press_enter()
        return

    assert isinstance(s, FixedGiftSet)

    print("\nЧто вы хотите изменить?")
    print("  1) Состав набора (пересобрать)")
    print("  2) Количество по позициям")
    print("  3) ID набора (gift_tg)")
    print("  4) Название")
    print("  0) Назад")

    choice = input_int("Ваш выбор: ", min_val=0, max_val=4, allow_cancel=True)
    if choice in (None, 0):
        press_enter()
        return

    if choice == 1:
        print_gifts_catalog(base)
        print(f"Вводите новые подарки по одному ({gift_min}–{gift_max}). Enter — закончить.")
        gift_keys: List[str] = []
        i = 1
        while i <= MAX_SET_SLOTS:
            val = input(f"Подарок №{i} — ID: ").strip()
            if _is_cancel_token(val):
                print("Отменено.")
                press_enter()
                return
            if val == "":
                break
            if not val.isdigit() or val not in base:
                print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
                continue
            gift_keys.append(val)
            i += 1

        if not gift_keys:
            print("Пусто — изменения не применены.")
        else:
            new_items: List[SetItem] = []
            for idx, gk in enumerate(gift_keys, start=1):
                qty = input_int(
                    f"{idx}. {base[gk]['title']} — новое количество (1–999): ",
                    min_val=1, max_val=999, allow_cancel=True,
                )
                if qty is None:
                    print("Отменено.")
                    press_enter()
                    return
                new_items.append(SetItem(gift_key=gk, qty=qty))
            s.items = new_items
            changed = True

    elif choice == 2:
        if not s.items:
            print("У набора нет позиций.")
        else:
            for it in s.items:
                qty = input_int(
                    f"{base[it.gift_key]['title']} — новое количество (сейчас {it.qty}): ",
                    min_val=1, max_val=999, allow_cancel=True,
                )
                if qty is None:
                    print("Отменено.")
                    press_enter()
                    return
                it.qty = qty
            changed = True

    elif choice == 3:
        while True:
            new_id = input_int(f"Новый ID (число ≥ {min_set_id}): ", min_val=min_set_id, allow_cancel=True)
            if new_id is None:
                print("Отменено.")
                press_enter()
                return
            new_id_s = str(new_id)
            if new_id_s in sets and new_id_s != s.key:
                print("Этот ID уже используется.")
                continue
            if new_id_s != s.key:
                sets.pop(s.key)
                s.key = new_id_s
                sets[new_id_s] = s
                changed = True
            break

    elif choice == 4:
        new_title = input_str("Новое название: ", allow_empty=False, allow_cancel=True)
        if new_title is None:
            print("Отменено.")
            press_enter()
            return
        s.title = new_title
        changed = True

    if changed:
        summary, _ = summarize_fixed_set(s, base)
        print("\nОбновлено:")
        print(summary)
        if yes_no("Сохранить"):
            save_sets(sets)
            print("✅ Изменения сохранены.")
        else:
            print("Не сохранено.")
    else:
        print("Изменений нет.")
    press_enter()

def cmd_delete_set():
    base = load_base_gifts()
    sets = load_sets()
    min_set_id = _calc_min_set_id(base)

    print("\n=== 🗑️ Удаление набора ===")
    sid = choose_existing_set_id(sets, min_set_id=min_set_id)
    if not sid:
        press_enter()
        return

    if yes_no(f"Удалить набор {sid} безвозвратно"):
        sets.pop(sid, None)
        save_sets(sets)
        print("✅ Удалено.")
    else:
        print("Отменено.")
    press_enter()

def cmd_list_sets():
    base = load_base_gifts()
    sets = load_sets()

    print("\n=== 📚 Список наборов ===")
    if not sets:
        print("Пока нет ни одного набора.")
        press_enter()
        return

    for k in sorted(sets.keys(), key=lambda x: int(x) if x.isdigit() else 10**18):
        s = sets[k]
        if isinstance(s, ChoiceGiftSet):
            summary, _ = summarize_choice_set(s, base)
        else:
            summary, _ = summarize_fixed_set(s, base)
        print(summary)
        print(f"Использование в лоте FunPay: gift_tg: {k}")
        print("-" * 48)

    press_enter()

def main_menu():
    while True:
        print("\n==============================")
        print("      Главное меню         ")
        print("==============================")
        print("Отмена везде: 0 / «отмена» / «назад».")
        print("\nДействия:")
        print("  1) Создать обычный набор (фикс состав)")
        print("  2) Создать набор-выбор (покупатель выбирает 1 подарок)")
        print("  3) Редактировать набор")
        print("  4) Удалить набор")
        print("  5) Посмотреть наборы")
        print("  0) Выход\n")

        choice = input_int("Выберите пункт (0–5): ", min_val=0, max_val=5, allow_cancel=True)
        if choice in (None, 0):
            print("Выход.")
            break

        if choice == 1:
            cmd_create_fixed_set()
        elif choice == 2:
            cmd_create_choice_set()
        elif choice == 3:
            cmd_edit_set()
        elif choice == 4:
            cmd_delete_set()
        elif choice == 5:
            cmd_list_sets()

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nВыход (Ctrl+C).")
