# Run from an elevated PowerShell as Steve. Never writes credentials to output.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator rights are required to install tasks that run while signed out.'
}
if ((Get-TimeZone).Id -ne 'GMT Standard Time') { throw 'Set Cyberdyne timezone to UK before installing.' }
$root = 'D:\Services\FPL-Scheduler'
New-Item -ItemType Directory -Force $root | Out-Null
# Restrict the scripts AND machine-encrypted credential before writing it.
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
foreach ($sid in @('S-1-5-18','S-1-5-32-544',$identity.User.Value)) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $root -AclObject $acl
New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-FplSchedule.ps1') -Destination $root -Force
# Reuse the existing signed-in GitHub credential; do not request or expose X/Telegram secrets.
$env:GCM_INTERACTIVE = 'never'
$credentialLines = "protocol=https`nhost=github.com`nusername=JustGeary`n`n" | & 'C:\Program Files\Git\cmd\git.exe' credential fill
if ($LASTEXITCODE -ne 0) { throw 'No usable saved GitHub credential. Sign into Git Credential Manager first.' }
$passwordLine = $credentialLines | Where-Object { $_.StartsWith('password=') } | Select-Object -First 1
if (-not $passwordLine) { throw 'GitHub credential unavailable.' }
$token = $passwordLine.Substring(9)
Add-Type -AssemblyName System.Security
$encrypted = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($token),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
[IO.File]::WriteAllBytes((Join-Path $root 'github-token.bin'),$encrypted)
$token = $null; $passwordLine = $null; $credentialLines = $null
if (-not [Diagnostics.EventLog]::SourceExists('FPLPriceBot')) { New-EventLog -LogName Application -Source FPLPriceBot }
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
$exe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
foreach ($entry in @(@{Name='Trigger';Time='00:05'},@{Name='Check';Time='00:15'})) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\Invoke-FplSchedule.ps1`" -Mode $($entry.Name)"
    $trigger = New-ScheduledTaskTrigger -Daily -At $entry.Time
    Register-ScheduledTask -TaskName "FPL Price Bot - $($entry.Name)" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'UK local time. Cloud execution remains on GitHub; independent GitHub cron fallback retained.' -Force | Out-Null
}
$probeAction = New-ScheduledTaskAction -Execute $exe -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\Invoke-FplSchedule.ps1`" -Mode Probe"
Register-ScheduledTask -TaskName 'FPL Price Bot - Probe' -Action $probeAction -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'FPL Price Bot - Probe'
Write-Output 'Installed 00:05 trigger and 00:15 completion check as SYSTEM. Read-only authentication probe started.'
