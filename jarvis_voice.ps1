Add-Type -AssemblyName System.Speech

$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$recognizer.SetInputToDefaultAudioDevice()

$grammar = New-Object System.Speech.Recognition.DictationGrammar
$recognizer.LoadGrammar($grammar)

$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speak.SelectVoice("Microsoft David Desktop")
$speak.Rate = 0
$speak.Volume = 100

Write-Host "JARVIS ishga tushdi."
$speak.Speak("Hello Farid. I am Jarvis.")

while ($true) {
    Write-Host "Gapiring..."

    $result = $recognizer.Recognize()

    if ($null -eq $result) {
        continue
    }

    $text = $result.Text
    Write-Host "Siz aytdingiz:" $text

    if ($text -match "exit|stop|goodbye") {
        $speak.Speak("Goodbye Farid.")
        break
    }

    $javob = "You said " + $text
    Write-Host "JARVIS:" $javob
    $speak.Speak($javob)
}
