$ErrorActionPreference='Stop'
try {
    if ((Get-TimeZone).Id -ne 'GMT Standard Time') { throw 'UK timezone required' }
    $root='D:\Services\FPL-Scheduler'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-PriceWatch.ps1') -Destination $root -Force
    $principal=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
    $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
    $trigger=New-ScheduledTaskTrigger -Daily -At ([datetime]'2026-09-14T08:00:00')
    $trigger.StartBoundary='2026-09-14T08:00:00'
    $trigger.EndBoundary='2026-09-21T00:00:00'
    $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\Invoke-PriceWatch.ps1`" -Mode morning"
    Register-ScheduledTask -TaskName 'FPL Price Watch - morning' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    $task=Get-ScheduledTask -TaskName 'FPL Price Watch - morning'
    $info=$task|Get-ScheduledTaskInfo
    @{Name=$task.TaskName;User=$task.Principal.UserId;NextRun=[string]$info.NextRunTime;Start=$task.Triggers[0].StartBoundary;End=$task.Triggers[0].EndBoundary}|ConvertTo-Json|Set-Content -Encoding UTF8 D:\Services\FPL-watch-morning-install.json
    # Before the first eligible review date this is a safe guard test, no dispatch or message.
    if ((Get-Date).ToString('yyyy-MM-dd') -eq '2026-09-13') { Start-ScheduledTask -TaskName 'FPL Price Watch - morning' }
} catch {
    $_|Out-String|Set-Content D:\Services\FPL-watch-morning-error.txt
    exit 1
}
