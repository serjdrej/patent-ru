Этот каталог — УСТАРЕВШЕЕ расположение ключа. Начиная с этой версии скилла
ключ ROSPATENT_API_KEY хранится вне дерева скилла:

  Windows: %LOCALAPPDATA%\rospatent\rospatent_key.enc (Windows DPAPI)
  macOS:   Keychain текущего пользователя, служба "rospatent-api-key"

Так ключ не зависит от того, куда и сколько раз установлен/переустановлен
скилл (библиотека навыков, другая папка, другая копия репозитория).

Если у вас ещё остался файл rospatent_key.enc в этом каталоге (со старой
версии) — ничего делать не нужно: scripts\rospatent.ps1 сам перенесёт его
в %LOCALAPPDATA%\rospatent при первом запуске.

Разовая настройка с нуля:
  Windows: cd scripts && .\rospatent-set-key.ps1   (дальше: .\rospatent.ps1 <команда>)
  macOS:   cd scripts && ./rospatent-set-key.sh     (дальше: ./rospatent.sh <команда>)

Оба скрипта спрашивают ключ скрытым вводом (как пароль) и сохраняют его
зашифрованным средствами самой ОС — не открытым текстом.

Этот каталог целиком в .gitignore — ничего отсюда никогда не попадёт
в git-репозиторий, даже случайно, кроме этого README.

Что защищают DPAPI и Keychain, а что нет — см. комментарии в начале
scripts\rospatent-set-key.ps1 / scripts/rospatent-set-key.sh и
references\search-rospatent.md.
