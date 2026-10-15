# Richtet den Autostart ein: Verknüpfung "Vinted-Bot" im Windows-Autostart-Ordner.
# Der Bot startet dann bei jeder Anmeldung unsichtbar (pythonw = ohne Konsolenfenster).
#
# Aufruf (aus dem Projektordner):
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_autostart.ps1 [-StartNow]
#
# Rückgängig machen: scripts\windows\uninstall_autostart.ps1

param([switch]$StartNow)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonw = Join-Path $project ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
  throw "Nicht gefunden: $pythonw - zuerst die venv einrichten (siehe README)."
}
if (-not (Test-Path (Join-Path $project ".env"))) {
  throw "Keine .env im Projektordner - zuerst ausfüllen (siehe README)."
}

$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "Vinted-Bot.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m vinted_bot"
$shortcut.WorkingDirectory = $project  # dort liegen .env, data\ und logs\
$shortcut.Description = "Vinted-Monitoring-Bot (Discord)"
$shortcut.WindowStyle = 7  # minimiert
$shortcut.Save()
Write-Host "Autostart eingerichtet: $link"

if ($StartNow) {
  Start-Process -FilePath $pythonw -ArgumentList "-m", "vinted_bot" -WorkingDirectory $project
  Write-Host "Bot gestartet. Log: $(Join-Path $project 'logs\bot.log')"
}
