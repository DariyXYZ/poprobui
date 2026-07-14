import json
import os
from filelock import FileLock

# Wallet state is owned exclusively by the api service — bot and miniapp both
# go through api's HTTP endpoints instead of touching this file directly.
# api and bot deploy as separate services/containers (see README), so a path
# shared across folders would silently desync balances between them.
DATA_DIR = os.getenv("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
BALANCE_DB_PATH = os.getenv("BALANCE_DB_PATH", os.path.join(DATA_DIR, "balances.json"))


def format_money(amount: int) -> str:
    rub = amount // 100
    kop = amount % 100
    return f"{rub} ₽" if kop == 0 else f"{rub},{kop:02d} ₽"


def load_balances() -> dict:
    try:
        with open(BALANCE_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_balances(data: dict) -> None:
    os.makedirs(os.path.dirname(BALANCE_DB_PATH), exist_ok=True)
    tmp_path = BALANCE_DB_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, BALANCE_DB_PATH)


def balance_of(user_id: int) -> int:
    with FileLock(BALANCE_DB_PATH + ".lock"):
        return int(load_balances().get(str(user_id), 0))


def add_balance(user_id: int, amount: int) -> int:
    with FileLock(BALANCE_DB_PATH + ".lock"):
        data = load_balances()
        key = str(user_id)
        data[key] = int(data.get(key, 0)) + amount
        save_balances(data)
        return data[key]


def debit_balance(user_id: int, amount: int) -> int | None:
    with FileLock(BALANCE_DB_PATH + ".lock"):
        data = load_balances()
        key = str(user_id)
        current = int(data.get(key, 0))
        if current < amount:
            return None
        data[key] = current - amount
        save_balances(data)
        return data[key]
