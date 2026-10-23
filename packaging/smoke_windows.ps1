# Boot-check a built Windows bundle, from outside the process.
#
# A windowed (no-console) exe has no console of its own, so nothing it prints can
# be seen unless it is redirected; and if Windows itself refuses to load it
# (missing DLL), the OS shows a modal dialog that nobody is watching - the process
# just sits there. So: run --version first (no window needed) to tell "cannot
# start at all" apart from "GUI hangs", capture stdout/stderr of both runs, and if
# anything has to be killed, report the window title so an error dialog can be
# identified.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File smoke_windows.ps1 `
#     -Exe dist\ShunyaNetSentinel\ShunyaNetSentinel.exe -SmokeLimitSec 300
#
# Exit codes: 0 ok | 2 --version timed out | 3 --smoke-test timed out |
#             4 --version exited non-zero | otherwise the smoke process's code.

param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [int]$VersionLimitSec = 60,
    [int]$SmokeLimitSec = 300,
    [string]$LogDir = $env:RUNNER_TEMP,
    # Where the app keeps its state and writes its own boot report. These must be
    # Windows paths: a POSIX path from git-bash points nowhere for a native process,
    # and the report would silently vanish.
    [string]$SentinelHome = "",
    [string]$SentinelSmokeFile = ""
)

if ($SentinelHome) { $env:SENTINEL_HOME = $SentinelHome }
if ($SentinelSmokeFile) { $env:SENTINEL_SMOKE_FILE = $SentinelSmokeFile }

if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Output "MISSING EXE: $Exe"
    exit 127
}

function Invoke-Bounded {
    param([string]$Arguments, [string]$Tag, [int]$LimitSec)

    $outFile = Join-Path $LogDir "$Tag.stdout.txt"
    $errFile = Join-Path $LogDir "$Tag.stderr.txt"
    $proc = Start-Process -FilePath $Exe -ArgumentList $Arguments -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput $outFile `
        -RedirectStandardError $errFile

    if (-not $proc.WaitForExit($LimitSec * 1000)) {
        $title = "unknown"
        try { $title = $proc.MainWindowTitle } catch { }
        Write-Output "$Tag TIMED OUT after ${LimitSec}s (window title: '$title')"
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch { }
        return 2
    }
    Write-Output "$Tag exited $($proc.ExitCode)"
    return $proc.ExitCode
}

function Show-Captured {
    param([string]$Tag)
    foreach ($suffix in @("stdout", "stderr")) {
        $file = Join-Path $LogDir "$Tag.$suffix.txt"
        if ((Test-Path -LiteralPath $file) -and ((Get-Item $file).Length -gt 0)) {
            Write-Output "--- $Tag $suffix ---"
            Get-Content -LiteralPath $file | Select-Object -Last 60 | ForEach-Object { Write-Output $_ }
        }
    }
}

Write-Output "bundle: $Exe"
Write-Output "state dir: $($SentinelHome -or '(default)')  report: $($SentinelSmokeFile -or '(default)')"
$versionRc = Invoke-Bounded -Arguments "--version" -Tag "version" -LimitSec $VersionLimitSec
Show-Captured -Tag "version"
if ($versionRc -eq 2) {
    Write-Output "RESULT: the bundle cannot start at all (no window, no output). This is a load/startup failure, not a GUI problem."
    exit 2
}
if ($versionRc -ne 0) {
    Write-Output "RESULT: --version failed with exit $versionRc"
    exit 4
}

$smokeRc = Invoke-Bounded -Arguments "--smoke-test" -Tag "smoke" -LimitSec $SmokeLimitSec
Show-Captured -Tag "smoke"
if ($smokeRc -eq 2) {
    Write-Output "RESULT: --version worked but --smoke-test hung; the window title above names whatever dialog is blocking."
    exit 3
}
Write-Output "RESULT: smoke process exited $smokeRc"
exit $smokeRc
