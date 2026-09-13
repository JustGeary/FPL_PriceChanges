$ErrorActionPreference='Stop'
try {
    $root='D:\Services\FPL-Scheduler'
    if ((Get-TimeZone).Id -ne 'GMT Standard Time') { throw 'UK timezone required' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-PriceWatch.ps1') -Destination $root -Force
    $principal=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
    $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
    foreach ($mode in @('alerts','roundup','review')) {
        $triggers=@()
        if ($mode -eq 'alerts') {
            for ($minute=480; $minute -le 1380; $minute+=30) {
                $time=([datetime]'2026-09-13').AddMinutes($minute)
                $t=New-ScheduledTaskTrigger -Daily -At $time
                $t.StartBoundary=$time.ToString('yyyy-MM-ddTHH:mm:ss')
                $t.EndBoundary='2026-09-20T00:00:00'
                $triggers+=$t
            }
        } else {
            $at=if ($mode -eq 'review') {'2026-09-20T08:00:00'} else {'2026-09-13T23:30:00'}
            $t=if ($mode -eq 'review') {New-ScheduledTaskTrigger -Once -At ([datetime]$at)} else {New-ScheduledTaskTrigger -Daily -At ([datetime]$at)}
            $t.StartBoundary=$at
            $t.EndBoundary=if ($mode -eq 'review') {'2026-09-21T00:00:00'} else {'2026-09-20T00:00:00'}
            $triggers+= $t
        }
        $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\Invoke-PriceWatch.ps1`" -Mode $mode"
        Register-ScheduledTask -TaskName "FPL Price Watch - $mode" -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
    }
    $report=foreach ($mode in @('alerts','roundup','review')) {
        $task=Get-ScheduledTask -TaskName "FPL Price Watch - $mode"
        $info=$task | Get-ScheduledTaskInfo
        [pscustomobject]@{Name=$task.TaskName;User=$task.Principal.UserId;NextRun=[string]$info.NextRunTime;Times=@($task.Triggers | ForEach-Object {$_.StartBoundary});Ends=@($task.Triggers | ForEach-Object {$_.EndBoundary})}
    }
    $report | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 D:\Services\FPL-watch-install.json
    # Authorized first grouped threshold digest; no individual-player test messages.
    Start-ScheduledTask -TaskName 'FPL Price Watch - alerts'
} catch {
    $_ | Out-String | Set-Content D:\Services\FPL-watch-install-error.txt
    exit 1
}
