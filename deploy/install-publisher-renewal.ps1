param(
    [string]$Python = 'D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe',
    [datetime]$FirstRun = (Get-Date).Date.AddDays(20).AddHours(9)
)

$ErrorActionPreference = 'Stop'
$taskName = 'SUSTechCampusPublisher-Renewal'
$runner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'run-publisher-renewal.ps1')).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$pwshPath = (Get-Command pwsh -ErrorAction Stop).Source
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($runner.Contains('"') -or $pythonPath.Contains('"')) { throw 'Invalid executable path.' }

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and ($existing.Description -notlike 'SUSTechCampusDashboard publisher metadata renewal.*')) {
    throw 'The task name belongs to another application; refusing to overwrite it.'
}
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Python "{1}"' -f $runner, $pythonPath
$action = New-ScheduledTaskAction -Execute $pwshPath -Argument $arguments -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval 20 -At $FirstRun
# Interactive uses the signed-in publisher's DPAPI context without saving a
# Windows password. Missed runs catch up when that user is next available.
$principal = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -RestartCount 48 -RestartInterval (New-TimeSpan -Hours 1)
$description = 'SUSTechCampusDashboard publisher metadata renewal. Every 20 days; local DPAPI keys; independent of Codex.'
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description $description -Force | Out-Null
$installed = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
if ($installed.Triggers[0].DaysInterval -ne 20 -or -not $installed.Settings.StartWhenAvailable) {
    throw 'Installed background renewal settings did not match.'
}
[pscustomobject]@{
    Task = $taskName
    IntervalDays = $installed.Triggers[0].DaysInterval
    State = [string]$installed.State
    StartWhenAvailable = $installed.Settings.StartWhenAvailable
    RequiresSignedInPublisher = $true
    RequiresCodex = $false
    NextRun = $info.NextRunTime.ToString('o')
} | ConvertTo-Json
