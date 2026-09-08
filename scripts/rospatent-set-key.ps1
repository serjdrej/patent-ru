# Разово: сохраняет ROSPATENT_API_KEY зашифрованным через Windows DPAPI
# (System.Security.Cryptography.ProtectedData, область CurrentUser).
#
# Что это даёт: файл .secrets\rospatent_key.enc на диске — не текст ключа,
# а шифротекст. Расшифровать его может ТОЛЬКО эта же учётная запись Windows
# на ТОЛЬКО этом же компьютере (DPAPI использует ключ, производный от вашего
# логина Windows). Если файл случайно попадёт в бэкап, в облачную синхронизацию,
# на флешку, в скриншот каталога — сам по себе он бесполезен.
#
# Чего это НЕ даёт: любая другая программа, запущенная под вашей же учётной
# записью Windows (не обязательно этот скилл), технически может вызвать те же
# DPAPI-функции и расшифровать файл — DPAPI защищает данные НА ДИСКЕ, а не
# от других процессов вашего собственного сеанса. Изоляция «только для этого
# скрипта» технически недостижима без полноценного секрет-менеджера
# (аппаратный токен, изолированный сервис с ACL) — см. references/search-rospatent.md.
#
# Запуск (в своём PowerShell, не через агента — ключ вводится скрыто, как пароль):
#   .\rospatent-set-key.ps1

$ErrorActionPreference = "Stop"

$secretsDir = Join-Path $PSScriptRoot "..\.secrets"
New-Item -ItemType Directory -Force -Path $secretsDir | Out-Null
$keyFile = Join-Path $secretsDir "rospatent_key.enc"

$secure = Read-Host -Prompt "Вставьте ключ ROSPATENT_API_KEY (ввод не отображается на экране)" -AsSecureString
if ($secure.Length -eq 0) {
    Write-Error "Пустой ввод — ключ не сохранён."
}

$encrypted = $secure | ConvertFrom-SecureString
Set-Content -LiteralPath $keyFile -Value $encrypted -Encoding utf8 -NoNewline

Write-Host "Сохранено (зашифровано DPAPI, CurrentUser): $keyFile"
Write-Host "Расшифровать сможет только эта учётная запись Windows на этом компьютере."
Write-Host "Дальше используйте: .\rospatent.ps1 <команда>"
