# Probe: open a .pptx in PowerPoint via COM, export slides to PNG, measure.
# Called from tools/render_probe.py (ADR-0013: COM via PowerShell, no pywin32).
# Output: ASCII key=value lines, one per line, invariant decimal format.
param(
    [Parameter(Mandatory=$true)][string]$Deck,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [int]$TargetWidth = 1280,
    [int]$QuitWaitSeconds = 30
)

$ErrorActionPreference = 'Stop'
# Кодировка вывода задаётся явно: иначе консоль отдаёт нелатинские пути в
# кодировке системы, а Python декодирует их как UTF-8 и падает. Ровно это
# сделано в mimeo/verify/powerpoint_probe.ps1 — здесь недоставало.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$inv = [System.Globalization.CultureInfo]::InvariantCulture

function Emit($k, $v) { Write-Output ("{0}={1}" -f $k, $v) }
function Num($d) { return ([math]::Round($d, 2)).ToString($inv) }

# Guard: PowerPoint is a single-instance COM server. If the user already has it
# open, New-Object attaches to THAT instance and Quit() would close their work.
$pre = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
Emit 'preexisting_powerpnt' $pre.Count
if ($pre.Count -gt 0) {
    Emit 'preexisting_pids' (($pre | ForEach-Object { $_.Id }) -join ',')
    Emit 'result' 'ABORTED_USER_INSTANCE_RUNNING'
    exit 3
}

if (-not (Test-Path $Deck)) { Emit 'result' 'NO_DECK'; exit 4 }
$Deck = (Resolve-Path $Deck).Path
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$OutDir = (Resolve-Path $OutDir).Path

Emit 'deck' $Deck
Emit 'deck_bytes' (Get-Item $Deck).Length

$app = $null
$pres = $null
$ownPid = 0
try {
    $t0 = Get-Date
    $app = New-Object -ComObject PowerPoint.Application
    Emit 'app_start_s' (Num ((Get-Date) - $t0).TotalSeconds)
    Emit 'version' $app.Version
    Emit 'alert_level_default' $app.DisplayAlerts

    # ppAlertsAll = 2: we WANT a warning to surface, not be swallowed.
    $app.DisplayAlerts = 2

    $now = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
    if ($now.Count -eq 1) { $ownPid = $now[0].Id }
    Emit 'own_pid' $ownPid

    # Open(FileName, ReadOnly, Untitled, WithWindow), MsoTriState -1 / 0
    $t0 = Get-Date
    $pres = $app.Presentations.Open($Deck, -1, 0, 0)
    Emit 'open_s' (Num ((Get-Date) - $t0).TotalSeconds)
    Emit 'opened' 'YES'
    Emit 'slides' $pres.Slides.Count
    Emit 'slide_w_pt' $pres.PageSetup.SlideWidth
    Emit 'slide_h_pt' $pres.PageSetup.SlideHeight
    Emit 'fonts_used' $pres.Fonts.Count
    if ($pres.Fonts.Count -gt 0) {
        Emit 'font_names' ((1..$pres.Fonts.Count | ForEach-Object { $pres.Fonts.Item($_).Name }) -join '|')
    }

    $ratio = $pres.PageSetup.SlideHeight / $pres.PageSetup.SlideWidth
    $w = $TargetWidth
    $h = [int][math]::Round($w * $ratio)
    Emit 'png_size' ("{0}x{1}" -f $w, $h)

    $each = @()
    $t0 = Get-Date
    for ($i = 1; $i -le $pres.Slides.Count; $i++) {
        $t1 = Get-Date
        $png = Join-Path $OutDir ("slide-{0:d2}.png" -f $i)
        $pres.Slides.Item($i).Export($png, 'PNG', $w, $h)
        $each += (Num ((Get-Date) - $t1).TotalSeconds)
    }
    Emit 'export_total_s' (Num ((Get-Date) - $t0).TotalSeconds)
    Emit 'export_each_s' ($each -join ',')

    $pngs = @(Get-ChildItem -Path $OutDir -Filter 'slide-*.png' | Sort-Object Name)
    Emit 'png_count' $pngs.Count
    Emit 'png_bytes' (($pngs | ForEach-Object { $_.Length }) -join ',')

    $pres.Close()
    $pres = $null
    $app.Quit()
    Emit 'result' 'OK'
}
catch {
    Emit 'result' 'ERROR'
    Emit 'error' ($_.Exception.Message -replace "`r?`n", ' / ')
    try { if ($pres) { $pres.Close() } } catch {}
    try { if ($app) { $app.Quit() } } catch {}
}
finally {
    if ($pres) { try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($pres) } catch {} }
    if ($app)  { try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) } catch {} }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}

# Quit() returns before the process is gone. Measure how long it actually takes:
# VERIFY must wait for exit, not assume Quit() is synchronous.
$t0 = Get-Date
$gone = $false
while (((Get-Date) - $t0).TotalSeconds -lt $QuitWaitSeconds) {
    if (-not (Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)) { $gone = $true; break }
    Start-Sleep -Milliseconds 200
}
Emit 'exit_wait_s' (Num ((Get-Date) - $t0).TotalSeconds)
Emit 'process_exited' $(if ($gone) { 'YES' } else { 'NO' })
$post = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
Emit 'leftover_powerpnt' $post.Count
if ($post.Count -gt 0) { Emit 'leftover_pids' (($post | ForEach-Object { $_.Id }) -join ',') }
