# Beezy voice-over kit: turns lines.json into one audio clip per scene, then zips them.
# Only reads lines.json and writes into this folder (plus installs the edge-tts Python package if Python exists).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$out = Join-Path $here "clips"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory $out | Out-Null
$lines = Get-Content (Join-Path $here "lines.json") -Raw -Encoding UTF8 | ConvertFrom-Json

# 1) Natural neural voice through edge-tts, if Python is installed
$py = $null
foreach ($c in @("py", "python", "python3")) {
  try { & $c -c "import sys; print(sys.version)" *> $null; if ($LASTEXITCODE -eq 0) { $py = $c; break } } catch {}
}
$neural = $false
if ($py) {
  Write-Host "Python found ($py). Installing edge-tts for a natural voice..."
  & $py -m pip install --quiet --user edge-tts
  if ($LASTEXITCODE -eq 0) {
    $neural = $true
    foreach ($l in $lines) {
      Write-Host "Scene $($l.id): $($l.scene)"
      $txt = Join-Path $out "$($l.id).txt"
      [IO.File]::WriteAllText($txt, $l.text, (New-Object Text.UTF8Encoding $false))
      & $py -m edge_tts --voice en-US-AndrewNeural --rate=+4% --file $txt --write-media (Join-Path $out "$($l.id).mp3")
      if ($LASTEXITCODE -ne 0) { $neural = $false; break }
      Remove-Item $txt
    }
  }
}

# 2) Fallback: the voice built into Windows
if (-not $neural) {
  Write-Host "Using the built-in Windows voice."
  Get-ChildItem $out | Remove-Item -Force
  Add-Type -AssemblyName System.Speech
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $pick = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name } | Where-Object { $_ -match "Aria|Guy|Jenny|David|Mark|Zira" } | Select-Object -First 1
  if ($pick) { $s.SelectVoice($pick) }
  $s.Rate = 1
  foreach ($l in $lines) {
    Write-Host "Scene $($l.id): $($l.scene)"
    $s.SetOutputToWaveFile((Join-Path $out "$($l.id).wav"))
    $s.Speak($l.text)
  }
  $s.SetOutputToNull(); $s.Dispose()
}

$zip = Join-Path $here "beezy_voice_clips.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $out "*") -DestinationPath $zip
Write-Host ""
Write-Host "Done. Attach this file to the Claude chat:" -ForegroundColor Yellow
Write-Host $zip -ForegroundColor Yellow
explorer.exe "/select,`"$zip`""
