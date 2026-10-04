$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$verificationRoot = Join-Path $projectRoot 'verification'
New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null
Add-Type -AssemblyName System.Speech
$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synthesizer.SetOutputToWaveFile((Join-Path $verificationRoot 'speech.wav'))
    $synthesizer.Speak('Welcome to Local Transcriber. This application runs speech recognition entirely on your computer. Your recordings stay private. You can edit the transcript and export subtitles. Vulkan provides acceleration for AMD graphics cards. Thank you for testing this local transcription application.')
} finally {
    $synthesizer.Dispose()
}
& ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'color=c=0x151c2b:s=640x360:r=25' -i (Join-Path $verificationRoot 'speech.wav') -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest (Join-Path $verificationRoot 'sample.mp4')
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not create the test MP4.' }
& ffmpeg -hide_banner -loglevel error -y -stream_loop 14 -i (Join-Path $verificationRoot 'speech.wav') -ar 16000 -ac 1 -c:a pcm_s16le (Join-Path $verificationRoot 'long.wav')
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not create the long test WAV.' }
Write-Output 'Created local synthetic fixtures in verification/. No remote media used.'
