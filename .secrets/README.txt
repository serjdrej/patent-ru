Ключ ROSPATENT_API_KEY хранится здесь зашифрованным через Windows DPAPI
(CurrentUser) — файл rospatent_key.enc, а не открытым текстом.

Разовая настройка:
  cd scripts
  .\rospatent-set-key.ps1
Скрипт спросит ключ скрытым вводом (как пароль) и сохранит его сюда
зашифрованным. Дальше пользоваться: .\rospatent.ps1 <команда>.

Этот каталог целиком в .gitignore — ничего отсюда никогда не попадёт
в git-репозиторий, даже случайно, кроме этого README.

Что защищает DPAPI, а что нет — см. комментарий в начале
scripts\rospatent-set-key.ps1 и references\search-rospatent.md.
