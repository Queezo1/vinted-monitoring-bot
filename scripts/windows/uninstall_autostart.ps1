# Entfernt den Autostart wieder. Ein gerade laufender Bot wird NICHT beendet
# (dafür: scripts\windows\stop_bot.ps1).

$link = Join-Path ([Environment]::GetFolderPath("Startup")) "Vinted-Bot.lnk"
if (Test-Path $link) {
  Remove-Item $link
  Write-Host "Autostart entfernt."
} else {
  Write-Host "Kein Autostart eingerichtet."
}
