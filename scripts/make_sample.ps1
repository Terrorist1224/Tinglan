$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path '.tmp/samples' | Out-Null
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speaker.SelectVoice('Microsoft Zira Desktop')
    $speaker.SetOutputToWaveFile("$PWD/.tmp/samples/english.wav")
    $speaker.Speak('Welcome to Tinglan. This is a local subtitle editor. We can listen carefully, correct the words, and translate each sentence. The original video stays on this computer.')
} finally { $speaker.Dispose() }
& tools/ffmpeg.exe -v error -y -f lavfi -i color=c=0x173c46:s=960x540:r=24 -i .tmp/samples/english.wav -shortest -c:v libx264 -preset ultrafast -c:a aac -movflags +faststart '.tmp/samples/听澜样片.mp4'
if ($LASTEXITCODE) { throw '样片生成失败。' }
