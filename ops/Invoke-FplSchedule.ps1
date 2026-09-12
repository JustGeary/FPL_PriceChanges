param([ValidateSet('Trigger','Check','Probe')][string]$Mode = 'Probe')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$repo = 'JustGeary/FPL_PriceChanges'
$base = "https://api.github.com/repos/$repo"
$zone = [TimeZoneInfo]::FindSystemTimeZoneById('GMT Standard Time')
$day = [TimeZoneInfo]::ConvertTimeFromUtc([datetime]::UtcNow, $zone).ToString('yyyy-MM-dd')
$log = Join-Path $root "logs\$day-$Mode.json"
Add-Type -AssemblyName System.Security
function Record([string]$Status, [string]$Detail) {
    @{ date=$day; mode=$Mode; at=[datetime]::UtcNow.ToString('o'); status=$Status; detail=$Detail } |
        ConvertTo-Json | Set-Content -Encoding UTF8 $log
}
try {
    $bytes = [IO.File]::ReadAllBytes((Join-Path $root 'github-token.bin'))
    $token = [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($bytes,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine))
    $headers = @{Authorization="Bearer $token"; Accept='application/vnd.github+json'; 'X-GitHub-Api-Version'='2022-11-28'; 'User-Agent'='Cyberdyne-FPL-Scheduler'}
    if ($Mode -eq 'Probe') {
        $workflow = Invoke-RestMethod -Uri "$base/actions/workflows/fpl-price-check.yml" -Headers $headers
        if ($workflow.state -ne 'active') { throw 'Production workflow is not active' }
        Record 'ready' 'Unattended GitHub authentication and workflow access verified.'
        exit 0
    }
    if ($Mode -eq 'Trigger') {
        # Only suppress a trigger when a current-date COMPLETE ledger exists.
        try {
            $file = Invoke-RestMethod -Uri "$base/contents/data/delivery/$day.json?ref=main" -Headers $headers
            $state = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($file.content)) | ConvertFrom-Json
            if ($state.date -eq $day -and $state.status -eq 'complete') { Record 'complete' 'Already delivered; no dispatch needed.'; exit 0 }
        } catch {
            if ([int]$_.Exception.Response.StatusCode -ne 404) { throw }
        }
    }
    $runMode = if ($Mode -eq 'Check') {'check'} else {'deliver'}
    $body = @{ref='main'; inputs=@{mode=$runMode}} | ConvertTo-Json -Compress
    # A dispatch is not proof of completion. Check mode creates a read-only GitHub run
    # which fails visibly if delivery has not completed (and never publishes anything).
    Invoke-RestMethod -Method Post -Uri "$base/actions/workflows/fpl-price-check.yml/dispatches" -Headers $headers -ContentType 'application/json' -Body $body | Out-Null
    Record 'dispatched' "GitHub $runMode requested. Verify result in Actions."
    if ($Mode -eq 'Check') {
        $file = Invoke-RestMethod -Uri "$base/contents/data/delivery/$day.json?ref=main" -Headers $headers
        $state = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($file.content)) | ConvertFrom-Json
        if ($state.date -ne $day -or $state.status -ne 'complete') { throw 'Delivery is not complete; check GitHub Actions.' }
        Record 'complete' 'Daily delivery ledger confirms completion.'
    }
} catch {
    # Never include raw HTTP exceptions or headers in logs.
    Record 'needs_attention' 'GitHub access, dispatch or completion check failed. Inspect Actions and the scheduled task result.'
    Write-EventLog -LogName Application -Source 'FPLPriceBot' -EntryType Error -EventId 1001 -Message "FPL $Mode failed for $day. See $log and GitHub Actions." -ErrorAction SilentlyContinue
    exit 1
} finally {
    $token = $null
    $headers = $null
}
