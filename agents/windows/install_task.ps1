<#
Registers "IT-Deck Agent" as a Windows Scheduled Task that starts this
folder's agent.py (via pythonw.exe, so no console window) at user logon.

Runs as the CURRENT interactive user, never SYSTEM: the agent depends on
pycaw/comtypes (Windows COM audio APIs) to control mic/volume, and a task
running as SYSTEM executes in session 0, which cannot see or control the
logged-in user's own audio session -- it has to run in the interactive
user session, per CLAUDE.md's Platform constraints on the Windows agent.

Safe to re-run: an existing "IT-Deck Agent" task is removed and recreated
rather than erroring on a duplicate name.

Internal identifiers in this repo (folder name, logging namespaces,
sqlite filename) intentionally stay "controlhub" per CLAUDE.md -- this
script and its comments can reference that name freely; only the task's
own display name uses the public "IT-Deck" branding.
#>

$ErrorActionPreference = "Stop"

$TaskName = "IT-Deck Agent"
$AgentDir = $PSScriptRoot

$pythonwCmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $pythonwCmd) {
    throw "pythonw.exe not found on PATH. Install Python (with 'Add python.exe to PATH' checked) before running this script."
}

# Idempotent: drop any prior registration first so re-running this script
# updates the task in place instead of failing on a duplicate name.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing '$TaskName' task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $pythonwCmd.Source -Argument "agent.py" -WorkingDirectory $AgentDir

# "At log on" scoped to the current user specifically -- not "any user
# logs on", not a SYSTEM trigger.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

# Interactive + Limited: runs in the user's own desktop session with
# standard (non-admin) rights, matching how agent.py is meant to run.
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

# Windows Task Scheduler rejects a sub-1-minute RestartInterval -- a 30s
# value was tried here and failed live ("Interval:PT30S" registration
# error), so 1 minute is the floor, not a rounding quirk to work around.
# ExecutionTimeLimit zero = no limit. The default is 72 hours, after which
# Task Scheduler stops the agent -- a long-running process, not a job.
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null

Write-Host "Registered scheduled task '$TaskName' for user '$currentUser'."
Write-Host "  Command: `"$($pythonwCmd.Source)`" agent.py"
Write-Host "  Working directory: $AgentDir"
Write-Host "It will start automatically at your next login. To start it now, run:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
