#Requires -Version 7.4
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string[]]$Arguments
)
# Only ephemeral GitHub runners: never create accounts on a maintainer's PC.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'This standard-user acceptance runner is restricted to GitHub Windows jobs.'
}
$workspace = (Resolve-Path $env:GITHUB_WORKSPACE).Path
$suffix = [guid]::NewGuid().ToString('N').Substring(0, 10)
$name = 'CampusCI' + $suffix
$identity = "$env:COMPUTERNAME\$name"
$reportRoot = Join-Path $env:RUNNER_TEMP ('campus-user-' + $suffix)
New-Item -ItemType Directory -Path $reportRoot | Out-Null
$password = ConvertTo-SecureString ('Aa1!' + [guid]::NewGuid().ToString('N')) -AsPlainText -Force
$credential = [pscredential]::new($identity, $password)
$created = $false
try {
    New-LocalUser -Name $name -Password $password -AccountNeverExpires | Out-Null
    $created = $true
    $users = Get-LocalGroup -SID 'S-1-5-32-545'
    Add-LocalGroupMember -Group $users -Member $name
    & icacls.exe $workspace '/grant:r' "${identity}:(OI)(CI)RX" '/T' '/C' '/Q' | Out-Null
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Source read permission setup failed: $code" }
    & icacls.exe $reportRoot '/grant:r' "${identity}:(OI)(CI)M" '/Q' | Out-Null
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Report permission setup failed: $code" }
    $profiles = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList').ProfilesDirectory)
    $profilePath = Join-Path $profiles $name
    $childEnv = @{}
    Get-ChildItem Env: | Where-Object Name -Match '^(SUSTECH_|GH_TOKEN$|GITHUB_TOKEN$|ACTIONS_.*TOKEN)' | ForEach-Object { $childEnv[$_.Name] = $null }
    $childEnv.USERPROFILE = $profilePath
    $childEnv.USERNAME = $name
    $childEnv.USERDOMAIN = $env:COMPUTERNAME
    $childEnv.APPDATA = Join-Path $profilePath 'AppData\Roaming'
    $childEnv.LOCALAPPDATA = Join-Path $profilePath 'AppData\Local'
    $childEnv.TEMP = Join-Path $profilePath 'AppData\Local\Temp'
    $childEnv.TMP = $childEnv.TEMP
    $childEnv.PYTHONUTF8 = '1'
    $childEnv.PYTHONIOENCODING = 'utf-8'
    function Invoke-StandardPython([string[]]$Values, [string]$Label) {
        # Windows command-line quoting, including embedded quotes and trailing slashes.
        $quoted = foreach ($value in $Values) {
            $value = $value.Replace('__USER_TEMP__', $childEnv.TEMP)
            '"' + (($value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
        }
        $stdout = Join-Path $reportRoot ($Label + '-stdout.log')
        $stderr = Join-Path $reportRoot ($Label + '-stderr.log')
        Write-Host "START standard-user $Label"
        $process = Start-Process -FilePath $Python -ArgumentList ($quoted -join ' ') -Credential $credential -LoadUserProfile -Environment $childEnv -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -Wait -PassThru
        $process.Refresh()
        Get-Content $stdout -Encoding utf8
        Get-Content $stderr -Encoding utf8
        if ($process.ExitCode -ne 0) { throw "Standard-user $Label failed: $($process.ExitCode)" }
        Write-Host "PASS standard-user $Label"
    }
    Invoke-StandardPython @('deploy/verify_windows_ci_identity.py') 'identity'
    Invoke-StandardPython $Arguments 'acceptance'
}
finally {
    if ($created) { Remove-LocalUser -Name $name }
    $credential = $null
    $password.Dispose()
}
