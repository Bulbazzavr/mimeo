# Slides of a .pptx to PNG with PowerPoint via COM (Z-34, PLAN-11.0; ADR-0013:
# COM via PowerShell, no pywin32). Called from mimeo/audit/raster.py.
# Output: ASCII key=value lines, one per line; `png=` once per slide, in order.
param(
    [Parameter(Mandatory=$true)][string]$Deck,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [int]$Width = 1280,
    [int]$QuitWaitSeconds = 30
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Emit($k, $v) { Write-Output ("{0}={1}" -f $k, $v) }

# PowerPoint is a single-instance COM server: if the user has it open,
# New-Object attaches to THAT instance and Quit() would close their work.
$pre = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
if ($pre.Count -gt 0) {
    Emit 'preexisting_pids' (($pre | ForEach-Object { $_.Id }) -join ',')
    Emit 'result' 'BUSY'
    exit 3
}
if (-not (Test-Path $Deck)) { Emit 'result' 'NO_DECK'; exit 4 }
$Deck = (Resolve-Path $Deck).Path
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$OutDir = [System.IO.Path]::GetFullPath($OutDir)

$app = $null
$pres = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    # Open(FileName, ReadOnly, Untitled, WithWindow)
    $pres = $app.Presentations.Open($Deck, -1, 0, 0)
    $n = $pres.Slides.Count
    Emit 'slides' $n
    $w = $pres.PageSetup.SlideWidth
    $h = $pres.PageSetup.SlideHeight
    $height = [int][math]::Round($Width * $h / $w)
    for ($i = 1; $i -le $n; $i++) {
        $png = Join-Path $OutDir ("slide-{0:D2}.png" -f $i)
        $pres.Slides.Item($i).Export($png, 'PNG', $Width, $height)
        Emit 'png' $png
    }
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

# Quit() returns before the process is gone: wait for it, the next PowerPoint
# user (VERIFY, preview, PDF) must find the application closed.
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt $QuitWaitSeconds) {
    if (-not (Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 200
}
