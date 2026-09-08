# Обёртка вокруг rospatent.py: расшифровывает ROSPATENT_API_KEY из
# .secrets\rospatent_key.enc (зашифрован Windows DPAPI, см. rospatent-set-key.ps1)
# и передаёт его ТОЛЬКО дочернему процессу python.exe — на время одного вызова.
# После завершения переменная нигде не остаётся, включая это же окно PowerShell.
#
# Разовая настройка (один раз на этом компьютере):
#   .\rospatent-set-key.ps1
#
# Дальше — в любом новом окне PowerShell, без повторного ввода ключа:
#   .\rospatent.ps1 datasets
#   .\rospatent.ps1 search '(веха OR "измерительная штанга") AND инерциальн*'
#   .\rospatent.ps1 doc RU2794881C1
#
# Что защищает DPAPI, а что нет — см. комментарий в rospatent-set-key.ps1 и
# references/search-rospatent.md.

$ErrorActionPreference = "Stop"

$keyFile = Join-Path $PSScriptRoot "..\.secrets\rospatent_key.enc"

if (-not (Test-Path $keyFile)) {
    Write-Error "Ключ не настроен: $keyFile не найден. Запустите один раз: .\rospatent-set-key.ps1"
}

$encryptedText = Get-Content -LiteralPath $keyFile -Raw
$secure = $null
$bstr = [IntPtr]::Zero
$key = $null
try {
    $secure = ConvertTo-SecureString $encryptedText -ErrorAction Stop
} catch {
    Write-Error "Не удалось расшифровать $keyFile. Обычная причина — файл создан под другой учётной записью Windows или на другом компьютере. Пересоздайте: .\rospatent-set-key.ps1"
}

try {
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $key = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

if (-not $key) {
    Write-Error "Расшифрованный ключ пуст. Пересоздайте: .\rospatent-set-key.ps1"
}

# $env:... здесь действует только для дочернего процесса python.exe, запущенного этой
# командой, — не становится переменной окружения ни этого окна PowerShell целиком,
# ни системы. После выхода из блока значение очищается.
try {
    $env:ROSPATENT_API_KEY = $key
    python (Join-Path $PSScriptRoot "rospatent.py") @args
}
finally {
    $env:ROSPATENT_API_KEY = $null
    Remove-Item Env:\ROSPATENT_API_KEY -ErrorAction SilentlyContinue
    $key = $null
}
