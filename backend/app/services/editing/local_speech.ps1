# Trusted offline speech adapter. Request values remain JSON data, never shell expressions or SSML.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$speechSynth = $null
try {
    $requestText = [Console]::In.ReadToEnd()
    if ($requestText.Length -gt 16384) { throw 'Request too large' }
    $speechRequest = ConvertFrom-Json -InputObject $requestText
    Add-Type -AssemblyName System.Speech
    $speechSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $installed = @($speechSynth.GetInstalledVoices() | Where-Object { $_.Enabled })
    if ($installed.Length -gt 64) { throw 'Voice catalog exceeds bound' }
    if ($speechRequest.mode -eq 'catalog') {
        $voiceRows = @($installed | ForEach-Object { @{ name = $_.VoiceInfo.Name; culture = $_.VoiceInfo.Culture.Name } })
        ConvertTo-Json -InputObject @{ voices = $voiceRows } -Depth 4 -Compress
    } elseif ($speechRequest.mode -eq 'render') {
        $chosen = @($installed | Where-Object { $_.VoiceInfo.Name -ceq $speechRequest.voice.name -and $_.VoiceInfo.Culture.Name -ceq $speechRequest.voice.culture })
        if ($chosen.Length -ne 1 -or $speechRequest.text.Length -lt 1 -or $speechRequest.text.Length -gt 1000 -or $speechRequest.rate -lt -2 -or $speechRequest.rate -gt 2) { throw 'Invalid explicit voice intent' }
        $speechFolder = [System.IO.Path]::GetFullPath([string]$speechRequest.folder)
        if (-not [System.IO.Directory]::Exists($speechFolder) -or [System.IO.Directory]::GetFileSystemEntries($speechFolder).Length -ne 0) { throw 'Attempt folder must be empty' }
        $speechTarget = [System.IO.Path]::Combine($speechFolder, 'voice.wav')
        $speechSynth.SelectVoice([string]$speechRequest.voice.name)
        if ($speechSynth.Voice.Name -cne $speechRequest.voice.name -or $speechSynth.Voice.Culture.Name -cne $speechRequest.voice.culture) { throw 'Voice selection changed' }
        $speechSynth.Rate = [int]$speechRequest.rate
        $speechFormat = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
        $speechSynth.SetOutputToWaveFile($speechTarget, $speechFormat)
        $speechSynth.Speak([string]$speechRequest.text)
        $speechSynth.SetOutputToNull()
        ConvertTo-Json -InputObject @{ completed = $true } -Compress
    } else { throw 'Unknown local mode' }
} catch {
    [Console]::Out.WriteLine('{"error":"Local system speech failed"}')
    exit 1
} finally {
    if ($null -ne $speechSynth) { $speechSynth.Dispose() }
}
