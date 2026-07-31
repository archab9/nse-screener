# Create a Desktop shortcut that launches the screener.
#
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
#
# Targets pythonw.exe rather than python.exe so no console window sits behind the app.
# Falls back to the system Python if the project venv is missing, and says so.

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $Root 'run_app.py'
$IconPath = Join-Path $Root 'assets\nse-screener.ico'
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'

if (-not (Test-Path $Launcher)) {
    throw "run_app.py not found at $Launcher"
}

if (-not (Test-Path $Pythonw)) {
    Write-Warning "No venv at $Pythonw - falling back to the system pythonw."
    $found = Get-Command pythonw -ErrorAction SilentlyContinue
    if (-not $found) { throw 'pythonw.exe not found. Create the venv first.' }
    $Pythonw = $found.Source
}

if (-not (Test-Path $IconPath)) {
    Write-Warning "Icon missing - run: python scripts\make_icon.py"
    $IconPath = $Pythonw
}

$DesktopPath = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopPath 'NSE Screener.lnk'

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = '"' + $Launcher + '"'
$Shortcut.WorkingDirectory = $Root      # config/ and data/ resolve relative to this
$Shortcut.IconLocation = $IconPath
$Shortcut.Description = 'NSE two-stage equity screener'
$Shortcut.WindowStyle = 1
$Shortcut.Save()

Write-Output "Created: $ShortcutPath"
Write-Output "  target : $Pythonw"
Write-Output "  args   : $($Shortcut.Arguments)"
Write-Output "  workdir: $Root"
Write-Output "  icon   : $IconPath"
