#!/usr/bin/env python3
"""Кросс-платформенное хранение ROSPATENT_API_KEY вне дерева скилла.

Одна и та же логика для Windows и macOS, без PowerShell/bash-обёрток —
только стандартная библиотека Python (плюс системная утилита `security` на
macOS, которая идёт с самой ОС). Расположение ключа не зависит от того, куда
и сколько раз установлен сам скилл (библиотека навыков, отдельная папка,
другая копия репозитория) — настройка нужна один раз на компьютер.

    Windows: %LOCALAPPDATA%\\rospatent\\rospatent_key.enc — сырой шифротекст
             Windows DPAPI (CryptProtectData/CryptUnprotectData, область
             CurrentUser), вызван напрямую через ctypes — без PowerShell и
             без стороннего пакета pywin32.
    macOS:   Keychain текущего пользователя, служба "rospatent-api-key",
             через системную утилиту `security` (subprocess, без сторонних
             пакетов).
    Прочие ОС: не поддержано — используйте export ROSPATENT_API_KEY=<ключ>.

Что это даёт: ключ на диске/в Keychain — не открытый текст, а шифротекст под
контролем ОС, привязанный к вашей учётной записи и этому компьютеру.

Чего это НЕ даёт: изоляции «только для этого скрипта» — любой процесс,
запущенный под той же учётной записью на этом же компьютере, технически
способен вызвать те же системные функции (DPAPI) или запросить тот же
элемент Keychain по имени службы. Полная изоляция недостижима без отдельного
секрет-менеджера с ACL (аппаратный токен, изолированный сервис).

Точка входа: python rospatent.py set-key
"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

SERVICE = "rospatent-api-key"


def _account() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()


# --------------------------------------------------------------- Windows (DPAPI)
def _win_key_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "rospatent" / "rospatent_key.enc"


def _win_legacy_key_path() -> Path:
    # До перехода на общий Python-скрипт ключ хранился внутри дерева скилла,
    # зашифрованным PowerShell-обёрткой (ConvertTo/From-SecureString) —
    # формат отличается от того, что пишет этот модуль, см. _try_legacy_win_decode.
    return Path(__file__).resolve().parent.parent / ".secrets" / "rospatent_key.enc"


def _dpapi_call(func_name: str, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    func = getattr(ctypes.windll.crypt32, func_name)
    ok = func(ctypes.byref(in_blob), None, None, None, None,
              CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_call("CryptProtectData", data)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_call("CryptUnprotectData", data)


def _win_save(key: str) -> None:
    path = _win_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dpapi_protect(key.encode("utf-8")))


def _try_legacy_win_decode(raw: bytes) -> str | None:
    """Формат PowerShell ConvertFrom-SecureString: hex(DPAPI(UTF-16LE(ключ)))."""
    try:
        ciphertext = bytes.fromhex(raw.decode("ascii").strip())
        plaintext = _dpapi_unprotect(ciphertext)
        return plaintext.decode("utf-16-le").strip() or None
    except Exception:
        return None


def _win_load() -> str | None:
    path = _win_key_path()
    if path.exists():
        raw = path.read_bytes()
        try:
            key = _dpapi_unprotect(raw).decode("utf-8").strip()
            if key:
                return key
        except Exception:
            pass  # файл здесь мог остаться в старом PowerShell-формате
        key = _try_legacy_win_decode(raw)
        if key:
            _win_save(key)  # самолечение: пересохранить в текущем формате
            return key
        return None

    legacy = _win_legacy_key_path()
    if legacy.exists():
        key = _try_legacy_win_decode(legacy.read_bytes())
        if key:
            _win_save(key)
            print(f"[keystore] Ключ перенесён из {legacy} в {path} — "
                  f"новое расположение не зависит от установки скилла.", file=sys.stderr)
            return key
    return None


# --------------------------------------------------------------- macOS (Keychain)
def _mac_save_interactive() -> bool:
    """Просит и сохраняет ключ, не передавая его через argv.

    Умышленно НЕ принимает готовое значение ключа и НЕ использует `-w <значение>`:
    `security` в списке процессов другого пользователя того же логина виден как
    обычный `ps`-вывод, и значение `-w` было бы видно там же. Вместо этого `-w`
    оставлен без значения — `security` сам просит его с терминала (readpassphrase
    через /dev/tty, независимо от перенаправления stdin/stdout), поэтому ключ на
    этом пути ни разу не существует ни как аргумент процесса, ни как переменная
    Python. Требует реального терминала (интерактивный запуск, не CI/pipe).
    """
    try:
        result = subprocess.run(
            ["security", "add-generic-password", "-a", _account(), "-s", SERVICE, "-U"]
        )
    except FileNotFoundError:
        print("Утилита `security` не найдена.", file=sys.stderr)
        return False
    return result.returncode == 0


def _mac_load() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", _account(), "-s", SERVICE, "-w"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode == 0:
        return result.stdout.strip() or None
    return None


# ------------------------------------------------------------------------- API
def set_key_interactive() -> bool:
    """Спрашивает ключ (скрытым вводом) и сохраняет его. True — при успехе.

    Ввод и сохранение объединены в одну функцию намеренно: на macOS сам ввод
    делает `security` (см. `_mac_save_interactive`) — ключ никогда не становится
    строкой в этом процессе Python, так что отдельного шага "получить ключ, потом
    передать на сохранение" здесь для macOS в принципе нет.
    """
    if sys.platform == "win32":
        try:
            key = getpass.getpass(
                "Вставьте ключ ROSPATENT_API_KEY (ввод не отображается на экране): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nОтменено.", file=sys.stderr)
            return False
        if not key:
            print("Пустой ввод — ключ не сохранён.", file=sys.stderr)
            return False
        _win_save(key)
        return True
    if sys.platform == "darwin":
        print("Ключ спросит сама утилита security (стандартный скрытый ввод) — "
              "значение не должно появляться в списке процессов.")
        return _mac_save_interactive()
    print("Автоматическое хранение ключа поддержано только на Windows и macOS. "
          "На этой ОС используйте: export ROSPATENT_API_KEY=<ключ>", file=sys.stderr)
    return False


def load_key() -> str | None:
    """Ключ из хранилища ОС, или None, если его там нет.

    Побочный эффект на Windows: если ключ найден только в устаревшем
    PowerShell-формате (`.secrets/rospatent_key.enc` внутри дерева скилла или
    уже перенесённый файл в старом формате по новому пути), эта функция САМА
    пишет его на диск в текущем формате по новому пути ("самолечение" — см.
    `_win_load`), прежде чем его вернуть. Побочного эффекта нет, если ключ уже
    хранится в текущем формате или отсутствует вовсе.
    """
    if sys.platform == "win32":
        return _win_load()
    if sys.platform == "darwin":
        return _mac_load()
    return None


def describe_location() -> str:
    if sys.platform == "win32":
        return f"{_win_key_path()} (Windows DPAPI, CurrentUser)"
    if sys.platform == "darwin":
        return f"Keychain, служба «{SERVICE}», аккаунт «{_account()}»"
    return "не поддерживается на этой ОС — используйте переменную окружения"
