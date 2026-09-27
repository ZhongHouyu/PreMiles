# Diagnose the generated .docx with Word COM; write results to a log file.
# ASCII-only on purpose: Windows PowerShell parses .ps1 as ANSI/GBK by default,
# so no non-ASCII literals are allowed in this file.
param([string]$Path, [string]$LogPath, [string]$PdfOut)
$lines = New-Object System.Collections.Generic.List[string]
function Say($t) { $lines.Add([string]$t) | Out-Null }

$word = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    Say ("Word version : " + $word.Version)
    $doc = $word.Documents.Open($Path, $false, $true)
    Say "opened OK"

    $probes = @(
        @{ n = "Paragraphs"; e = { $doc.Paragraphs.Count } },
        @{ n = "Tables"; e = { $doc.Tables.Count } },
        @{ n = "InlineShapes"; e = { $doc.InlineShapes.Count } },
        @{ n = "OMaths"; e = { $doc.OMaths.Count } },
        @{ n = "Words"; e = { $doc.Words.Count } },
        @{ n = "Pages"; e = { $doc.ComputeStatistics(2) } }
    )
    foreach ($p in $probes) {
        try { Say ("{0,-13}: {1}" -f $p.n, (& $p.e)) }
        catch { Say ("{0,-13}: FAILED - {1}" -f $p.n, $_.Exception.Message) }
    }

    try {
        $txt = $doc.Content.Text
        Say ("Content chars : " + $txt.Length)
        $italicA = [char]::ConvertFromUtf32(0x1D44E)
        Say ("has math text : " + ($txt.Contains($italicA)))
        Say ("head 100      : " + $txt.Substring(0, [Math]::Min(100, $txt.Length)))
    } catch { Say ("read Content failed: " + $_.Exception.Message) }

    if ($PdfOut) {
        try { $doc.ExportAsFixedFormat($PdfOut, 17); Say ("exported PDF  : OK") }
        catch { Say ("export PDF failed: " + $_.Exception.Message) }
    }
    $doc.Close(0)
} catch {
    Say ("EXCEPTION: " + $_.Exception.Message)
} finally {
    if ($word) { try { $word.Quit() } catch {} }
    [System.IO.File]::WriteAllLines($LogPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
}
