# Export a .pptx to PDF with PowerPoint via COM (Z-27, PLAN-10.0; ADR-0013:
# COM via PowerShell, no pywin32). Called from mimeo/export/pdf.py.
# Output: ASCII key=value lines, one per line.
param(
    [Parameter(Mandatory=$true)][string]$Deck,
    [Parameter(Mandatory=$true)][string]$Pdf,
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
$dir = Split-Path -Parent $Pdf
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
$Pdf = [System.IO.Path]::GetFullPath($Pdf)

$app = $null
$pres = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    # Open(FileName, ReadOnly, Untitled, WithWindow)
    $pres = $app.Presentations.Open($Deck, -1, 0, 0)
    Emit 'slides' $pres.Slides.Count
    # ppSaveAsPDF = 32
    $pres.SaveAs($Pdf, 32)
    $pres.Close()
    $pres = $null
    $app.Quit()
    Emit 'pdf' $Pdf
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
# user (VERIFY, preview) must find the application closed.
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt $QuitWaitSeconds) {
    if (-not (Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 200
}
