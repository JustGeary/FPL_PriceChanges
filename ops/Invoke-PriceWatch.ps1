param([ValidateSet('alerts','roundup','review','morning')][string]$Mode='alerts')
$ErrorActionPreference='Stop'
$local=[TimeZoneInfo]::ConvertTimeFromUtc([datetime]::UtcNow,[TimeZoneInfo]::FindSystemTimeZoneById('GMT Standard Time'))
$day=$local.ToString('yyyy-MM-dd')
$minute=$local.Hour*60+$local.Minute
if ($Mode -eq 'morning') {
    if ($day -lt '2026-09-14' -or $day -gt '2026-09-20' -or $minute -lt 480) { exit 0 }
} elseif ($Mode -eq 'review') {
    if ($day -ne '2026-09-20') { exit 0 }
} else {
    if ($day -lt '2026-09-13' -or $day -ge '2026-09-20') { exit 0 }
    if ($Mode -eq 'alerts' -and ($minute -lt 480 -or $minute -ge 1395)) { exit 0 }
    if ($Mode -eq 'roundup' -and $minute -lt 1410) { exit 0 }
}
try {
    Add-Type -AssemblyName System.Security
    $token=[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'github-token.bin')),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine))
    $headers=@{Authorization="Bearer $token";Accept='application/vnd.github+json';'User-Agent'='Cyberdyne-FPL-Watch'}
    $body=@{ref='main';inputs=@{mode=$Mode}} | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri 'https://api.github.com/repos/JustGeary/FPL_PriceChanges/actions/workflows/price-watch.yml/dispatches' -Headers $headers -ContentType application/json -Body $body | Out-Null
    @{at=$local.ToString('o');mode=$Mode;status='dispatched'} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot "logs\$day-watch-$Mode.json")
} catch {
    @{at=$local.ToString('o');mode=$Mode;status='failed'} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot "logs\$day-watch-$Mode.json")
    Write-EventLog -LogName Application -Source FPLPriceBot -EntryType Error -EventId 1002 -Message 'FPL prediction trial dispatch failed; inspect GitHub Actions.' -ErrorAction SilentlyContinue
    exit 1
} finally { $token=$null; $headers=$null }
