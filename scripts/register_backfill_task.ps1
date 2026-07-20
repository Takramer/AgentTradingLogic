# Register (or re-register) the daily AlphaVantage earnings backfill as a
# Windows Scheduled Task. Idempotent (-Force overwrites). Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\register_backfill_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_backfill_task.ps1 -At 06:30
# Remove: Unregister-ScheduledTask -TaskName AgentTradingLogic-AVBackfill -Confirm:$false
#
# One run/day matches AlphaVantage's 25/day budget reset. Logs to
# av_cache\backfill.log; coverage in av_cache\_backfill_state.json.
param([string]$At = "06:00")

$ErrorActionPreference = "Stop"
$TaskName = "AgentTradingLogic-AVBackfill"
$Repo     = Split-Path -Parent $PSScriptRoot
$Cmd      = Join-Path $PSScriptRoot "av_backfill.cmd"
if (-not (Test-Path $Cmd)) { throw "wrapper not found: $Cmd" }

$action   = New-ScheduledTaskAction -Execute $Cmd -WorkingDirectory $Repo
$trigger  = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily AlphaVantage earnings backfill (SUE gate)" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
Write-Output ("Registered '{0}' daily at {1}. State={2}" -f $TaskName, $At, $t.State)
Write-Output ("Run now: Start-ScheduledTask -TaskName {0}" -f $TaskName)
