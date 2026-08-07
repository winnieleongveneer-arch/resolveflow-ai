<#
    new-run.ps1 — start a case and print the run id, ready to paste into Auto.

    Usage:
        .\scripts\new-run.ps1 ITSM-2237

    Why this exists: on the day you want one command and one line of output, not
    three commands and a wall of JSON to hunt through. The run id is also copied
    to the clipboard, so it is a straight Ctrl+V into the Auto form.
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $IssueKey,

    [string] $BaseUrl = "http://localhost:8001"
)

$body = @{ issue_key = $IssueKey; trigger_source = "manual" } | ConvertTo-Json -Compress

try {
    $run = Invoke-RestMethod -Uri "$BaseUrl/api/agent/runs" -Method Post `
                             -ContentType "application/json" -Body $body
}
catch {
    Write-Host ""
    Write-Host "  Could not reach the Command Center at $BaseUrl" -ForegroundColor Red
    Write-Host "  Is Docker running?  docker compose ps" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

# Auto's execute endpoint returns HTTP 500 platform-side, so a fresh run lands
# as FAILED. The row is still created and usable — the status corrects itself
# the moment an Operator calls the policy gate. Say so plainly rather than
# letting a red word on screen worry anyone mid-demo.
$autoNote = ""
if ($run.status -eq "FAILED" -and $run.error_message -like "*Auto returned HTTP 500*") {
    $autoNote = "Auto's execute endpoint is down platform-side; trigger the Operator from the Auto UI. This does not affect the run."
}

$run.id | Set-Clipboard

Write-Host ""
Write-Host "  Ticket    $($run.issue_key)"
Write-Host "  Run id    $($run.id)" -ForegroundColor Green
Write-Host "            (copied to clipboard)" -ForegroundColor DarkGray
if ($autoNote) {
    Write-Host ""
    Write-Host "  Note      $autoNote" -ForegroundColor DarkYellow
}
Write-Host ""
Write-Host "  Passport  $($BaseUrl -replace '8001','3001')/runs/$($run.id)" -ForegroundColor DarkGray
Write-Host ""
