#Requires -Version 5.0
<#
.SYNOPSIS
    IOptimal run wrapper — fixes Windows console encoding so Unicode box-drawing
    characters and emoji display correctly in PowerShell terminals.

.DESCRIPTION
    By default PowerShell decodes pipe output from external programs using the
    Windows OEM code page (CP850), which mangles UTF-8 output into mojibake.
    This wrapper sets both [Console]::OutputEncoding and $OutputEncoding to
    UTF-8 before running Python, ensuring clean output.

.EXAMPLE
    .\run.ps1 produce --car ferrari --ibt "ibt\session.ibt" --wing 14 --sto out.sto
    .\run.ps1 calibrate --car ferrari --status
    .\run.ps1 analyze --car ferrari --ibt "ibt\session.ibt"
#>

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PassThruArgs
)

# ── Fix PowerShell pipeline encoding ─────────────────────────────────────────
# This must happen BEFORE any pipe operation that reads from an external process.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding            = [System.Text.UTF8Encoding]::new($false)

# ── Find Python interpreter ──────────────────────────────────────────────────
$python = $null
$candidates = @(
    "C:\Users\tfunk\AppData\Local\Python\bin\python.exe",
    "python",
    "python3"
)
foreach ($p in $candidates) {
    if (Get-Command $p -ErrorAction SilentlyContinue) {
        # Verify it has numpy (the minimal requirement)
        $check = & $p -c "import numpy" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $python = $p
            break
        }
    }
}
if (-not $python) {
    Write-Error "No suitable Python interpreter found. Tried: $($candidates -join ', ')"
    exit 1
}

# ── Run IOptimal with UTF-8 forced end-to-end ───────────────────────────────
$env:PYTHONUTF8 = "1"
& $python __main__.py @PassThruArgs
