# Gemini TTS Voice Guide

## Recommended for Storytelling

| Voice | Character | Best For |
|-------|-----------|----------|
| **Kore** | Warm, clear, expressive | Default narrator, stories, fiction |
| **Aoede** | Smooth, melodic | Children's stories, poetry |
| **Charon** | Deep, measured | Epic/dramatic narration |
| **Puck** | Energetic, lively | Comedy, light stories |
| **Leda** | Gentle, soothing | Bedtime stories, calm narration |

## Usage
```python
from google.genai import types

config = types.GenerateContentConfig(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
        )
    ),
    temperature=2.0,
)
```

## All 30 Prebuilt Voices
Achernar, Achird, Algenib, Algieba, Alnilam, Aoede, Autonoe, Callirrhoe, Charon,
Despina, Enceladus, Erinome, Fenrir, Gacrux, Iapetus, Kore, Laomedeia, Leda,
Orus, Pulcherrima, Puck, Rasalgethi, Sadachbia, Sadaltager, Schedar, Sulafat,
Umbriel, Vindemiatrix, Zephyr, Zubenelgenubi

Case-insensitive.
