# Beendet den laufenden Bot. Die Prozess-ID steht in data\bot.lock (schreibt der Bot beim Start).
# Hartes Beenden ist unkritisch: Gefundene Artikel werden in einer Transaktion gespeichert,
# Ungesendetes wird beim nächsten Start nachgeholt.

$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$lockFile = Join-Path $project "data\bot.lock"
if (-not (Test-Path $lockFile)) {
  Write-Host "Keine Sperrdatei - der Bot läuft vermutlich nicht."
  exit 0
}

$botPid = (Get-Content $lockFile -Raw).Trim()
$process = Get-Process -Id $botPid -ErrorAction SilentlyContinue
# Sicherheitsprüfung: nur beenden, wenn es wirklich ein Python-Prozess ist
if ($null -eq $process -or $process.ProcessName -notmatch "^pythonw?$") {
  Write-Host "Der Bot läuft nicht (PID $botPid ist kein laufender Python-Prozess)."
  exit 0
}
Stop-Process -Id $botPid
Write-Host "Bot beendet (PID $botPid)."
