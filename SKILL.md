---
name: patent-ru
description: Russian patent prosecution and prior-art search skill — combines Rospatent (ИС ПП) database search, including the Soviet/Russian fund via the official search-platform API, with drafting and editing of application documents under FIPS requirements (order of the Russian Ministry of Economic Development No. 107, 21.02.2023). Use when the user mentions патентный поиск, поиск аналогов, база Роспатента, формула изобретения, заявка на изобретение, уведомление ФИПС, нарушение единства изобретения, выделенная заявка, ответ на запрос экспертизы, реферат, прототип, новая материя, п.2 ст.1378 ГК РФ, or asks to draft, review, or audit a Russian invention application. Two branches — search and drafting — never load together; GOST/ЕСКД drawing standards are out of scope.
---

# Патентный поиск и делопроизводство РФ (Роспатент / ФИПС)

Два независимых ветвления: **поиск** аналогов и **написание/редактура** документов заявки по Требованиям приказа Минэкономразвития № 107. Загружайте ровно один `references/*.md` под конкретную задачу — не всю ветвь и не обе ветви разом.

## Маршрутизация

| Задача | Загрузить |
|---|---|
| Найти аналоги/уровень техники по российскому и советскому фонду (RU/SU) | [references/search-rospatent.md](references/search-rospatent.md) |
| Найти аналоги за рубежом (Google Patents/Espacenet/USPTO) или когда доступа к API Роспатента нет | [references/search-international.md](references/search-international.md) |
| Спланировать поиск целиком: выбрать под-сценарий, вести журнал, вынести вердикт | [references/search-workflow.md](references/search-workflow.md) |
| Подготовить ответ на уведомление ФИПС / запрос экспертизы | [references/draft-office-action.md](references/draft-office-action.md) |
| Собрать выделенную заявку (ст.1381 ГК РФ) | [references/draft-divisional.md](references/draft-divisional.md) |
| Проверить документы на соответствие № 107 и на новую материю | [references/audit-compliance.md](references/audit-compliance.md) |
| Составить или отредактировать формулу изобретения | [references/draft-claims.md](references/draft-claims.md) |
| Составить заявку с нуля (описание, реферат, МПК, прототип) | [references/draft-application.md](references/draft-application.md) |
| Сверить формулировку или реквизиты применяемой нормы | [references/norms-registry.md](references/norms-registry.md) |

## Стыки — где допустимо два reference за раз

- «не порочит ли найденный аналог нашу формулу» → `search-*.md` + `draft-claims.md`
- «ответ на уведомление требует правки формулы» → `draft-office-action.md` + `draft-claims.md`
- «выделенная заявка требует собственной формулы» → `draft-divisional.md` + `draft-claims.md`

Во всех остальных случаях — один файл. Если задача не укладывается ни в одну строку таблицы, читайте только заголовок «Загружайте этот файл, когда: …» нужных кандидатов, а не их целиком.

## Пример дисциплины ключа

Ключ хранится централизованно на компьютере — не в дереве скилла и не привязан к тому, откуда и сколько раз скилл установлен (библиотека навыков, отдельная папка, другая копия репозитория). Перед первым вызовом проверить, настроен ли он, и вызывать API только через обёртку — не `rospatent.py` напрямую:

```bash
# Windows (PowerShell) — ключ в %LOCALAPPDATA%\rospatent\rospatent_key.enc (DPAPI)
Test-Path "$env:LOCALAPPDATA\rospatent\rospatent_key.enc"
.\scripts\rospatent.ps1 search '(веха OR "измерительная штанга") AND инерциальн*'

# macOS (bash/zsh) — ключ в Keychain, служба "rospatent-api-key"
security find-generic-password -a "$USER" -s rospatent-api-key -w >/dev/null 2>&1
./scripts/rospatent.sh search '(веха OR "измерительная штанга") AND инерциальн*'
```

## Жёсткие правила

- **Дисциплина ключа.** `ROSPATENT_API_KEY` читается только из переменной окружения, и её выставляют только обёртки `rospatent.ps1`/`rospatent.sh` — вызывать `rospatent.py` напрямую с ключом в команде не нужно и не следует. Никогда не печатать ключ, не логировать, не сохранять в JSON/отчётах/коде. Перед первым вызовом в сессии проверить, настроен ли ключ (команды выше). Если не настроен — **не запрашивать ключ в переписке**: показать пользователю ровно одну команду для его собственного терминала — `scripts/rospatent-set-key.ps1` (Windows) или `scripts/rospatent-set-key.sh` (macOS) — и подождать, пока он её выполнит сам. Повторять не нужно: настройка одноразовая на компьютер.
- **Не выдумывать нормы.** Реквизиты, цитаты и статус проверки норм — только из `references/norms-registry.md`. Перед подачей любого документа в ФИПС проверить применённые пункты по действующей редакции — реестр является рабочей опорой, а не источником истины.
- **ГОСТ вне периметра.** ЕСКД не пересказывается и не цитируется. Требования к чертежам берутся только из раздела V (пп.70–76) Требований № 107.
- **Формула:** независимый пункт не может ссылаться на зависимый (позиция экспертизы ФИПС) — см. `draft-claims.md`.
- **`similar_search` Роспатента** — не доказательство отсутствия аналогов, см. `search-rospatent.md`.
- **Плейсхолдеры** — видимые и одноформатные: `⟦ЗАПОЛНИТЬ: что именно, откуда взять⟧`.
- **Правовая оговорка.** Результат — поисковый сигнал или черновик текста, а не правовая позиция. Решение по стратегии, редакции формулы и индексу МПК — за патентным поверенным.

## Скрипты

| Скрипт | Роль |
|---|---|
| `scripts/rospatent.py` | клиент API Роспатента: `search`/`doc`/`similar`/`datasets`/`ipc`, ретраи, три-счёт. Ключ берёт только из окружения — сам его нигде не хранит |
| `scripts/rospatent.ps1` / `scripts/rospatent.sh` | обёртка: достаёт ключ из DPAPI-файла (Windows) / Keychain (macOS) и передаёт `rospatent.py` только на время одного вызова — обычный способ запуска |
| `scripts/rospatent-set-key.ps1` / `scripts/rospatent-set-key.sh` | разовая настройка ключа на компьютере (см. «Дисциплина ключа» выше) |
| `scripts/claims_integrity.py` | ссылочная целостность формулы и безопасная перенумерация (`check` / `renum`) |
| `scripts/novelty_check.py` | новая материя (`ngram`) и неопределённые обозначения (`symbols`) |
| `scripts/google_patents.py` | fallback-поиск через `xhr/query` + дословный текст из PDF, минуя блокируемую `/patent/<ID>` (см. `search-international.md`) |
