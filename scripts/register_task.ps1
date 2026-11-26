$TaskName = "SvitloSk-AddressMonitor"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ScriptPath = Join-Path $Root "scripts\run_monitor.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""

$Trigger1 = New-ScheduledTaskTrigger -Daily -At "07:30"
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "19:30"

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($Trigger1, $Trigger2) `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Щоденний моніторинг бази адрес і підчерг SvitloSk (07:30 та 19:30)" `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (daily at 07:30 and 19:30)."
Get-ScheduledTask -TaskName $TaskName | Format-Table TaskName, State -AutoSize
