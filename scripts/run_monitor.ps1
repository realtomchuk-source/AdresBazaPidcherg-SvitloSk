param(
    [switch]$SkipFetch,
    [switch]$AcceptMassChange
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("monitor_" + (Get-Date -Format "yyyyMMdd") + ".log")

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Show-Toast([string]$Title, [string]$Body) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
        $xml = @"
<toast>
  <visual>
    <binding template="ToastText02">
      <text id="1">$([System.Security.SecurityElement]::Escape($Title))</text>
      <text id="2">$([System.Security.SecurityElement]::Escape($Body))</text>
    </binding>
  </visual>
</toast>
"@
        $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
        $doc.LoadXml($xml)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
        $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    } catch {
        Write-Log "Toast failed: $($_.Exception.Message)"
    }
}

$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Log "ERROR: Python venv not found at $PythonExe"
    Show-Toast "SvitloSk Monitor: помилка" "Не знайдено .venv\Scripts\python.exe"
    exit 3
}

$env:PYTHONIOENCODING = "utf-8"
$pyArgs = @("src\monitor.py", "--profile", "profiles\hromada.json")
if ($SkipFetch) { $pyArgs += "--skip-fetch" }
if ($AcceptMassChange) { $pyArgs += "--accept-mass-change" }

Write-Log "=== Starting monitor: $PythonExe $($pyArgs -join ' ') ==="
$output = & $PythonExe @pyArgs 2>&1
$exitCode = $LASTEXITCODE
foreach ($line in $output) {
    Write-Log "$line"
}
Write-Log "=== Finished with exit code $exitCode ==="

$reportPath = Join-Path $Root "output\run_report.json"
$report = $null
if (Test-Path $reportPath) {
    try { $report = Get-Content -Path $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
}

switch ($exitCode) {
    0 {
        $chg = 0
        if ($report -and $report.changes -and $report.changes.subqueue_changed) {
            $chg = [int]$report.changes.subqueue_changed
        }
        if ($chg -gt 0) {
            Show-Toast "SvitloSk: базу оновлено" "Виявлено змін підчерг: $chg"
        }
    }
    10 {
        Write-Log "No changes in source file (unchanged)."
    }
    5 {
        Write-Log "Skipped: another instance is already running (lock active)."
    }
    2 {
        $errMsg = if ($report -and $report.error) { $report.error } else { "guard_failed" }
        $hint = if ($errMsg -match "max_changed_ratio") { " Запустіть із --accept-mass-change." } else { "" }
        Show-Toast "SvitloSk: оновлення зупинено" "$errMsg$hint"
    }
    default {
        Show-Toast "SvitloSk: помилка ($exitCode)" "Деталі у лозі: logs\monitor_$(Get-Date -Format 'yyyyMMdd').log"
    }
}

# Rotation: logs > 30 days, raw XLSX > last 10, state backups > last 14
Get-ChildItem -Path $LogDir -Filter "monitor_*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$rawDir = Join-Path $Root "data\raw_oblenergo"
if (Test-Path $rawDir) {
    Get-ChildItem -Path $rawDir -Filter "*.xlsx" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 10 |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

$backupDir = Join-Path $Root "data\state\backup"
if (Test-Path $backupDir) {
    Get-ChildItem -Path $backupDir -Filter "addresses_*.json" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 14 |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

exit $exitCode
