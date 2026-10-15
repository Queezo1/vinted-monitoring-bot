# Startet den Bot unsichtbar im Hintergrund (wie der Autostart).
# Läuft er schon, beendet sich der zweite Start sofort von selbst.

$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonw = Join-Path $project ".venv\Scripts\pythonw.exe"
Start-Process -FilePath $pythonw -ArgumentList "-m", "vinted_bot" -WorkingDirectory $project
Write-Host "Bot gestartet. Log: $(Join-Path $project 'logs\bot.log')"
