#!/bin/bash
# Обёртка вокруг rospatent.py: достаёт ROSPATENT_API_KEY из Keychain (см.
# rospatent-set-key.sh) и передаёт его ТОЛЬКО дочернему процессу python3 —
# на время одного вызова. После завершения переменная нигде не остаётся,
# включая эту же сессию терминала.
#
# Ключ хранится ВНЕ дерева скилла намеренно — не зависит от того, откуда и
# сколько раз скилл установлен/переустановлен/скопирован (см. rospatent-set-key.sh).
#
# Разовая настройка (один раз на этом компьютере):
#   ./rospatent-set-key.sh
#
# Дальше — в любом новом окне терминала, без повторного ввода ключа:
#   ./rospatent.sh datasets
#   ./rospatent.sh search '(веха OR "измерительная штанга") AND инерциальн*'
#   ./rospatent.sh doc RU2794881C1
#
# Что защищает Keychain, а что нет — см. комментарий в rospatent-set-key.sh и
# references/search-rospatent.md.
#
# На Windows используйте вместо этого rospatent.ps1 (ключ из DPAPI-файла).

set -euo pipefail

SERVICE="rospatent-api-key"
ACCOUNT="$USER"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

KEY="$(security find-generic-password -a "$ACCOUNT" -s "$SERVICE" -w 2>/dev/null || true)"

if [ -z "$KEY" ]; then
    echo "Ключ не настроен: запись '$SERVICE' не найдена в Keychain. Запустите один раз: ./rospatent-set-key.sh" >&2
    exit 1
fi

PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"

export ROSPATENT_API_KEY="$KEY"
status=0
"$PYTHON_BIN" "$SCRIPT_DIR/rospatent.py" "$@" || status=$?
unset ROSPATENT_API_KEY
unset KEY
exit $status
