# Run the local attachment download agent when this Windows user signs in.
$ErrorActionPreference = 'Stop'
$project = 'D:\Projects\SUSTechCampusDashboard'
$pythonw = 'D:\Caches\SUSTechCampusDashboard-venv\Scripts\pythonw.exe'
$launcher = Join-Path $project 'deploy\run_dashboard.pyw'
if (-not (Test-Path -LiteralPath $pythonw) -or -not (Test-Path -LiteralPath $launcher)) {
    throw 'Dashboard runtime or launcher is missing.'
}
$taskName = 'SUSTechCampusDashboard'
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $launcher + '"') -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
