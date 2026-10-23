# Boot-check a built Windows bundle, from outside the process.
#
# A windowed (no-console) exe has no console of its own, so nothing it prints can
# be seen unless it is redirected; and if Windows itself refuses to load it
# (missing DLL), the OS shows a modal dialog that nobody is watching - the process
# just sits there. So: run --version first (no window needed) to tell "cannot start
# at all" apart from "the GUI hangs", capture each run's output, print the hung
# process's window title before killing it, and fall back to the report the app
# writes for itself when even that produces nothing.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File smoke_windows.ps1 `
#     -Exe dist\ShunyaNetSentinel\ShunyaNetSentinel.exe -SmokeLimitSec 300
#
# Exit codes: 0 ok | 2 --version timed out | 3 --smoke-test timed out |
#             4 --version exited non-zero | otherwise the smoke process's code.
#
# All human-readable output goes through Write-Host: a function's return value is
# its exit code ONLY, because PowerShell turns anything written to the success
# stream into part of that value.

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
    Write-Host "MISSING EXE: $Exe"
    exit 127
}

# A hidden modal dialog is otherwise invisible in a build log: grab the screen and
# the text of every visible window so the error message becomes part of the record.
function Capture-DiagnosticScreen {
    param([string]$Tag)
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $png = Join-Path $LogDir "error-screen-$Tag.png"
        $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        Write-Host "screen captured: $png"
    } catch {
        Write-Host "screen capture failed: $_"
    }

    try {
        Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinText {
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    static string T(IntPtr h) { var sb = new StringBuilder(512); GetWindowTextW(h, sb, sb.Capacity); return sb.ToString(); }
    public static List<string> Dump() {
        var outp = new List<string>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            string title = T(h);
            if (string.IsNullOrEmpty(title)) return true;
            outp.Add("WINDOW: [" + title + "]");
            EnumChildWindows(h, (c, l2) => { string t = T(c); if (!string.IsNullOrEmpty(t)) outp.Add("   text: " + t); return true; }, IntPtr.Zero);
            return true;
        }, IntPtr.Zero);
        return outp;
    }
}
"@
        foreach ($line in [WinText]::Dump()) { Write-Host $line }
    } catch {
        Write-Host "window text enumeration failed: $_"
    }
}

function Show-Captured {
    param([string]$Tag)
    foreach ($suffix in @("stdout", "stderr")) {
        $file = Join-Path $LogDir "$Tag.$suffix.txt"
        if ((Test-Path -LiteralPath $file) -and ((Get-Item $file).Length -gt 0)) {
            Write-Host "--- $Tag $suffix ---"
            Get-Content -LiteralPath $file | Select-Object -Last 60 | ForEach-Object { Write-Host $_ }
        }
    }
}

function Show-ReportFile {
    param([string]$Path, [string]$Label)
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Write-Host "--- $Label ($Path) ---"
        Get-Content -LiteralPath $Path | ForEach-Object { Write-Host $_ }
        return $true
    }
    return $false
}

# Runs the exe and waits at most LimitSec. Returns the process exit code, or 2 if
# it had to be killed. With -NoRedirect the app keeps its own (absent) console -
# used as a fallback in case redirection is what blocks it.
function Invoke-Bounded {
    param([string]$Arguments, [string]$Tag, [int]$LimitSec, [switch]$NoRedirect)

    $startArgs = @{
        FilePath     = $Exe
        ArgumentList = $Arguments
        PassThru     = $true
        WindowStyle  = "Hidden"
    }
    if (-not $NoRedirect) {
        $startArgs.RedirectStandardOutput = (Join-Path $LogDir "$Tag.stdout.txt")
        $startArgs.RedirectStandardError  = (Join-Path $LogDir "$Tag.stderr.txt")
    }

    $proc = Start-Process @startArgs
    $exitedInTime = $proc.WaitForExit($LimitSec * 1000)
    if (-not $exitedInTime) {
        $title = "unknown"
        try { $title = $proc.MainWindowTitle } catch { }
        Write-Host "$Tag TIMED OUT after ${LimitSec}s (window title: '$title')"
        Capture-DiagnosticScreen -Tag $Tag
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch { }
        return 2
    }
    # The timed overload can return before ExitCode is populated; the parameterless
    # wait blocks until it is. Without this, a perfectly good run reports an empty
    # exit code and gets treated as a failure.
    $proc.WaitForExit()
    $code = $proc.ExitCode
    if ($null -eq $code) { $code = 0 }
    Write-Host "$Tag exited $code"
    return [int]$code
}

Write-Host "bundle: $Exe"
if ($SentinelHome) { Write-Host "state dir: $SentinelHome" } else { Write-Host "state dir: (default)" }
if ($SentinelSmokeFile) { Write-Host "report file: $SentinelSmokeFile" } else { Write-Host "report file: (default)" }

# ---- 1) --version: no window, no Qt. Proves the exe can be loaded and Python runs.
$versionRc = Invoke-Bounded -Arguments "--version" -Tag "version" -LimitSec $VersionLimitSec
Show-Captured -Tag "version"

$versionRc = [int]$versionRc
if ($versionRc -eq 2) {
    # Try again with nothing redirected: if the bootloader blocks on a redirected
    # handle, this run gets through and writes its own report.
    Write-Host "retrying --version without output redirection..."
    $retry = Invoke-Bounded -Arguments "--version" -Tag "version_noredirect" -LimitSec $VersionLimitSec -NoRedirect
    if ($SentinelSmokeFile) { Show-ReportFile -Path $SentinelSmokeFile -Label "app report (no-redirect retry)" | Out-Null }
    $beside = Join-Path (Split-Path -Parent $Exe) "sentinel_smoke.txt"
    Show-ReportFile -Path $beside -Label "app report beside exe" | Out-Null
    if ($retry -eq 0) {
        Write-Host "RESULT: the bundle runs when its output is NOT redirected - redirection was the hang."
        exit 0
    }
    Write-Host "RESULT: the bundle cannot start at all (no output, no report). That is a load/startup failure, not a GUI problem."
    exit 2
}
if ($versionRc -ne 0) {
    Write-Host "RESULT: --version failed with exit $versionRc"
    exit 4
}

# ---- 2) --smoke-test: boots the real interface offscreen and reports how far it got
$smokeRc = [int](Invoke-Bounded -Arguments "--smoke-test" -Tag "smoke" -LimitSec $SmokeLimitSec)
Show-Captured -Tag "smoke"
if ($SentinelSmokeFile) { Show-ReportFile -Path $SentinelSmokeFile -Label "app smoke report" | Out-Null }

if ($smokeRc -eq 2) {
    Write-Host "RESULT: --version worked but --smoke-test hung; the window title above names whatever dialog is blocking."
    exit 3
}
Write-Host "RESULT: smoke process exited $smokeRc"
exit $smokeRc
