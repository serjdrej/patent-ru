# Обёртка вокруг rospatent.py: читает ROSPATENT_API_KEY из локального файла
# .secrets\rospatent_key.txt (не из git, не из системной переменной среды Windows)
# и передаёт его ТОЛЬКО дочернему процессу python.exe — на весь остальной ПК
# ключ не распространяется и после завершения скрипта нигде не остаётся.
#
# Разовая настройка:
#   1. Создайте файл  .secrets\rospatent_key.txt
#   2. Одной строкой вставьте туда ключ, сохраните
#   Дальше — просто запускайте этот скрипт вместо rospatent.py напрямую.
#
# Использование — те же аргументы, что у rospatent.py:
#   .\rospatent.ps1 datasets
#   .\rospatent.ps1 search '(веха OR "измерительная штанга") AND инерциальн*'
#   .\rospatent.ps1 doc RU2794881C1

$ErrorActionPreference = "Stop"

$keyFile = Join-Path $PSScriptRoot "..\.secrets\rospatent_key.txt"

if (-not (Test-Path $keyFile)) {
    Write-Error @"
Файл с ключом не найден: $keyFile

Создайте его один раз:
  1. New-Item -ItemType Directory -Force (Join-Path `$PSScriptRoot '..\.secrets') | Out-Null
  2. Откройте .secrets\rospatent_key.txt в блокноте и вставьте туда ваш ключ ИС ПП
     Роспатента одной строкой (без кавычек).

Ключ выдаётся в личном кабинете ИС ПП, раздел «Генерирование ключей».
"@
}

$key = (Get-Content -LiteralPath $keyFile -Raw).Trim()
if (-not $key) {
    Write-Error "Файл $keyFile пуст. Вставьте туда ключ одной строкой и сохраните."
}

# $env:... внутри блока { } действует только для дочернего процесса python.exe,
# запущенного этой командой, — не становится переменной окружения ни этого
# PowerShell-окна целиком, ни системы. После выхода из блока значение исчезает.
try {
    $env:ROSPATENT_API_KEY = $key
    python (Join-Path $PSScriptRoot "rospatent.py") @args
}
finally {
    $env:ROSPATENT_API_KEY = $null
    Remove-Item Env:\ROSPATENT_API_KEY -ErrorAction SilentlyContinue
}
