# Gemini TTS API Quick Reference

> For the full canonical reference, see: `docs/03_Operations/123_Gemini_TTS_Source_Of_Truth_2026-04-22.md`

## API Endpoint
```
POST https://texttospeech.googleapis.com/v1/text:synthesize
```

## Auth
- **VPS**: Service account at `/opt/universal_agent/.gcp-tts-sa-key.json`
- **Desktop**: `gcloud auth print-access-token`
- **Header**: `Authorization: Bearer <token>`
- **Project**: `x-goog-user-project: gen-lang-client-0229532959`

## Models
| Model | Default? | Notes |
|-------|----------|-------|
| `gemini-3.1-flash-tts-preview` | ✅ | Best controllability |
| `gemini-2.5-flash-tts` | | GA, fast |
| `gemini-2.5-pro-tts` | | Highest quality |
| `gemini-2.5-flash-lite-preview-tts` | | Cheapest |

## ❌ DO NOT USE
- `gemini-2.5-flash-preview-tts` — AI Studio preview name, WRONG API
- `GEMINI_IMAGE_API_KEY` — doesn't work for Cloud TTS
- `google.genai` with bare API key — wrong SDK for TTS
