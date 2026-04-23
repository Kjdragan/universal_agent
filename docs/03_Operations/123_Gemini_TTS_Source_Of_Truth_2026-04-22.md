# Gemini TTS: Source of Truth

> **Status**: Canonical source-of-truth  
> **Created**: 2026-04-22  
> **Last verified**: 2026-04-22 (all 4 models tested, Cloud TTS API confirmed working)  
> **GCP Project**: `gen-lang-client-0229532959` (Gemini API)

---

## 1. API Architecture — The Critical Decision

Google exposes Gemini TTS through **three different API paths**. Only two are correct for production use. This section exists because the wrong path was used in the initial implementation, causing recurring model-name confusion, unnecessary ffmpeg dependencies, and preview-only model access.

### API Comparison

| Aspect | Cloud Text-to-Speech API ⭐ | Vertex AI API | ❌ AI Studio (DO NOT USE) |
|--------|---------------------------|---------------|--------------------------|
| **SDK** | `google.cloud.texttospeech` | `google.genai` with `vertexai=True` | `google.genai` with API key |
| **Endpoint** | `texttospeech.googleapis.com` | `aiplatform.googleapis.com` | `generativelanguage.googleapis.com` |
| **Auth** | Service account / ADC | Service account + project ID | Bare API key |
| **Model names** | GA names (e.g., `gemini-2.5-flash-tts`) | GA names | Preview names only (e.g., `gemini-2.5-flash-preview-tts`) |
| **Output formats** | **MP3, OGG_OPUS, LINEAR16, ALAW, MULAW** | Raw PCM only | Raw PCM only |
| **Input structure** | Separate `text` + `prompt` (4KB each) | Single `contents` field | Single `contents` field |
| **Region support** | Regional endpoints | Regional via `location` param | No region control |
| **Streaming** | Multi-request, multi-response | Single-request, multi-response | N/A |
| **Temperature** | No | Yes (0.0–2.0] | Yes |

### Which API to Use

```
┌─────────────────────────────────────────────────────────┐
│ DEFAULT: Cloud Text-to-Speech API                       │
│ • Native MP3 output (no ffmpeg needed)                  │
│ • Separate prompt + text fields (8KB total budget)      │
│ • GA model names (no -preview- confusion)               │
│ • Best for: narration, audiobooks, TTS pipelines        │
├─────────────────────────────────────────────────────────┤
│ ALTERNATIVE: Vertex AI API                              │
│ • If you need temperature control                       │
│ • If you're already using Vertex AI for other models    │
│ • Output is PCM — requires client-side conversion       │
├─────────────────────────────────────────────────────────┤
│ ❌ NEVER USE: AI Studio (bare API key)                  │
│ • Preview models only (will 404 on GA model names)      │
│ • No region support (cannot access 3.1 models)          │
│ • PCM output only                                       │
│ • This was the source of all model-name confusion       │
└─────────────────────────────────────────────────────────┘
```

> **Why AI Studio fails**: AI Studio model names have `-preview-` inserted (e.g., `gemini-2.5-flash-preview-tts`). The Cloud TTS and Vertex AI APIs use GA names (e.g., `gemini-2.5-flash-tts`). An agent using AI Studio will discover the wrong model names and bake them into the skill, causing confusion on every subsequent run.

---

## 2. Model Registry

All models verified working on 2026-04-22 via Cloud TTS REST endpoint against project `gen-lang-client-0229532959`.

| Model ID | Status | Best For | Region Requirement | Test Result |
|----------|--------|----------|-------------------|-------------|
| **`gemini-3.1-flash-tts-preview`** | Preview | Latest, best controllability, multi-speaker | **`global` ONLY** | ✅ 27,936 bytes MP3 |
| `gemini-2.5-flash-tts` | **GA** | Low-latency narration, cost-efficient | All regions | ✅ 29,088 bytes MP3 |
| `gemini-2.5-pro-tts` | **GA** | Highest quality — audiobooks, podcasts | All regions | ✅ 27,360 bytes MP3 |
| `gemini-2.5-flash-lite-preview-tts` | Preview | Ultra cost-efficient, single-speaker only | All regions | ✅ 25,056 bytes MP3 |

### Model Selection Guidance

- **Default for narration skills**: `gemini-3.1-flash-tts-preview` — best style control via markup tags
- **For production audiobooks**: `gemini-2.5-pro-tts` — highest quality GA model
- **For high-volume/budget**: `gemini-2.5-flash-tts` — fast, cheap, reliable GA model
- **For cost-minimized**: `gemini-2.5-flash-lite-preview-tts` — cheapest, single-speaker only

### Region Requirements

> **CRITICAL**: `gemini-3.1-flash-tts-preview` is ONLY available in the `global` region. If you use a regional endpoint (e.g., `us-texttospeech.googleapis.com`), the 3.1 model will 404.

| Region | Endpoint | 3.1 Flash | 2.5 Flash | 2.5 Pro | 2.5 Lite |
|--------|----------|-----------|-----------|---------|----------|
| `global` | `texttospeech.googleapis.com` | ✅ | ✅ | ✅ | ✅ |
| `us` | `us-texttospeech.googleapis.com` | ❌ | ✅ | ✅ | ✅ |
| `eu` | `eu-texttospeech.googleapis.com` | ❌ | ✅ | ✅ | ✅ |

**Always use the `global` endpoint** unless you have data residency requirements.

---

## 3. Authentication

### Production (VPS): Service Account Key

The canonical auth method for VPS (`/opt/universal_agent`) is a GCP service account key stored in Infisical.

**Required permission**: `aiplatform.endpoints.predict` (granted via `roles/aiplatform.user` role)

**Infisical key**: `GCP_TTS_SERVICE_ACCOUNT_KEY` (JSON string, base64-encoded)

**Runtime flow:**
```python
import json, base64, tempfile, os, subprocess

# 1. Fetch from Infisical
result = subprocess.run(
    ['infisical', 'secrets', 'get', 'GCP_TTS_SERVICE_ACCOUNT_KEY',
     '--env=prod', '--path=/', '--plain'],
    capture_output=True, text=True
)
sa_key_b64 = result.stdout.strip()

# 2. Write to temp file
sa_key_json = base64.b64decode(sa_key_b64).decode()
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    f.write(sa_key_json)
    sa_key_path = f.name

# 3. Set env for ADC
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = sa_key_path
```

### Fallback: gcloud CLI ADC

If the service account key is unavailable, the VPS can fall back to gcloud CLI user auth:
```bash
gcloud auth application-default login --project=gen-lang-client-0229532959
```
This creates `~/.config/gcloud/application_default_credentials.json` which the SDK auto-discovers.

### Desktop: gcloud User Auth

The local desktop already has gcloud installed and authenticated as `kevinjdragan@gmail.com`. The project `gen-lang-client-0229532959` is the default project. ADC needs to be set up once:
```bash
gcloud auth application-default login --project=gen-lang-client-0229532959
```

### API Key Tiering (Legacy Reference)

For reference — the AI Studio API keys and their restrictions:

| Key Name | Can TTS? | Notes |
|----------|----------|-------|
| `GEMINI_API_KEY` | ❌ | Blocked for GenerativeService audio |
| `GEMINI_IMAGE_API_KEY` | ✅ (AI Studio only) | Works for preview TTS models via AI Studio |

**Do NOT use these keys for Cloud TTS API.** Cloud TTS uses OAuth2 / service account auth, not API keys.

---

## 4. Code Patterns

### Recommended: Cloud Text-to-Speech API (Synchronous)

```python
# google-cloud-texttospeech >= 2.29.0 required
# For multi-speaker freeform: >= 2.31.0
# For safety settings: >= 2.32.0
from google.cloud import texttospeech

client = texttospeech.TextToSpeechClient()
# For regional endpoint:
# from google.api_core.client_options import ClientOptions
# client = texttospeech.TextToSpeechClient(
#     client_options=ClientOptions(api_endpoint="texttospeech.googleapis.com")
# )

response = client.synthesize_speech(
    input=texttospeech.SynthesisInput(
        text="The cardboard box behind the dumpling shop...",     # up to 4,000 bytes
        prompt="Read in a warm, gentle audiobook narrator voice.", # up to 4,000 bytes
    ),
    voice=texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name="Aoede",
        model_name="gemini-3.1-flash-tts-preview",
    ),
    audio_config=texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,  # native MP3!
    ),
)

with open("output.mp3", "wb") as f:
    f.write(response.audio_content)  # already MP3 bytes
```

### Multi-Speaker Synthesis (Freeform)

```python
# google-cloud-texttospeech >= 2.31.0 required
multi_speaker_config = texttospeech.MultiSpeakerVoiceConfig(
    speaker_voice_configs=[
        texttospeech.MultispeakerPrebuiltVoice(
            speaker_alias="Narrator",
            speaker_id="Aoede",
        ),
        texttospeech.MultispeakerPrebuiltVoice(
            speaker_alias="Character",
            speaker_id="Charon",
        ),
    ]
)

voice = texttospeech.VoiceSelectionParams(
    language_code="en-US",
    model_name="gemini-3.1-flash-tts-preview",
    multi_speaker_voice_config=multi_speaker_config,
)

response = client.synthesize_speech(
    input=texttospeech.SynthesisInput(
        text="Narrator: The old dog lifted his head. Character: Who's there?",
        prompt="Read as a gentle bedtime story.",
    ),
    voice=voice,
    audio_config=texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=24000,
    ),
)
```

### Alternative: Vertex AI API

```python
from google import genai
from google.genai import types

client = genai.Client(vertexai=True, project="gen-lang-client-0229532959", location="global")
response = client.models.generate_content(
    model="gemini-3.1-flash-tts-preview",
    contents="Read in a warm narrator style: The cardboard box behind the dumpling shop...",
    config=types.GenerateContentConfig(
        speech_config=types.SpeechConfig(
            language_code="en-US",
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Aoede')
            )
        ),
        temperature=2.0,
    )
)
# Output is raw PCM — needs WAV wrapping + MP3 conversion
pcm_data = response.candidates[0].content.parts[0].inline_data.data
```

---

## 5. Voice Inventory

| Name | Gender | Character |
|------|--------|-----------|
| Achernar | Female | — |
| Achird | Male | — |
| Algenib | Male | — |
| Algieba | Male | — |
| Alnilam | Male | — |
| **Aoede** | **Female** | **Warm, narrative — recommended for audiobooks** |
| Autonoe | Female | — |
| **Callirrhoe** | **Female** | — |
| **Charon** | **Male** | **Deep, rich — recommended for male narration** |
| Despina | Female | — |
| Enceladus | Male | — |
| Erinome | Female | — |
| Fenrir | Male | — |
| Gacrux | Female | — |
| Iapetus | Male | — |
| **Kore** | **Female** | **Popular general-purpose voice** |
| Laomedeia | Female | — |
| **Leda** | **Female** | — |
| Orus | Male | — |
| Pulcherrima | Female | — |
| **Puck** | **Male** | — |
| Rasalgethi | Male | — |
| Sadachbia | Male | — |
| Sadaltager | Male | — |
| Schedar | Male | — |
| Sulafat | Female | — |
| Umbriel | Male | — |
| Vindemiatrix | Female | — |
| Zephyr | Female | — |
| Zubenelgenubi | Male | — |

**Note**: All voices for `gemini-3.1-flash-tts-preview` are in Preview status.

---

## 6. Limits and Constraints

| Constraint | Limit | Applies To |
|-----------|-------|------------|
| Text field | ≤ 4,000 bytes | Cloud TTS API |
| Prompt field | ≤ 4,000 bytes | Cloud TTS API |
| Text + prompt combined | ≤ 8,000 bytes | Cloud TTS API |
| Vertex AI `contents` field | ≤ 8,000 bytes | Vertex AI API |
| Output audio duration | ~655 seconds max (truncated if exceeded) | All APIs |
| Speaker aliases | Alphanumeric only, no whitespace | Multi-speaker |

### Chunking Strategy for Long Texts

For texts exceeding 4,000 bytes, chunk at natural boundaries:
1. **Paragraph breaks** first (`\n\n`)
2. **Sentence boundaries** as fallback (`.!?` followed by whitespace)
3. **Target chunk size**: 3,500 bytes (leaves room for markup tags)
4. Concatenate MP3 chunks using `ffmpeg -i "concat:chunk1.mp3|chunk2.mp3" output.mp3` or binary append for MP3

---

## 7. Markup Tags

These tags provide fine-grained control over speech delivery. Reliability is based on Google's official guidance.

### Non-Speech Sounds (Mode 1)
| Tag | Effect | Reliability |
|-----|--------|-------------|
| `[sigh]` | Audible sigh | High |
| `[laughing]` | Laughter | High |
| `[uhm]` | Hesitation sound | High |

### Style Modifiers (Mode 2)
| Tag | Effect | Reliability |
|-----|--------|-------------|
| `[sarcasm]` | Sarcastic tone | High |
| `[robotic]` | Robotic speech | High |
| `[shouting]` | Increased volume | High |
| `[whispering]` | Decreased volume | High |
| `[extremely fast]` | Increased speed | High |

### Pacing and Pauses (Mode 4)
| Tag | Effect | Duration | Reliability |
|-----|--------|----------|-------------|
| `[short pause]` | Brief pause | ~250ms | High |
| `[medium pause]` | Standard pause | ~500ms | High |
| `[long pause]` | Dramatic pause | ~1000ms+ | High |

### Usage for Narration
For audiobook narration, insert markup at natural boundaries:
- `[medium pause]` between paragraphs
- `[long pause]` at scene/chapter breaks
- `[short pause]` at emotional beats within paragraphs

---

## 8. Lessons Learned

### Lesson 1: AI Studio ≠ Cloud TTS ≠ Vertex AI
The three APIs have different model names, auth mechanisms, and output formats. An agent that learns model names from one API will get them wrong on another. **Always use Cloud TTS API for TTS tasks.**

### Lesson 2: Preview vs GA Model Naming
- AI Studio: `gemini-2.5-flash-preview-tts` (note `-preview-` after `flash`)
- Cloud TTS: `gemini-2.5-flash-tts` (no `-preview-`)
- Confusion: `gemini-3.1-flash-tts-preview` (note `-preview` at the END — this is the actual model name, not a preview variant of another model)

### Lesson 3: API Key Tiering
The `GEMINI_API_KEY` is blocked for audio generation. The `GEMINI_IMAGE_API_KEY` works for AI Studio but NOT for Cloud TTS. Cloud TTS uses OAuth2/service account auth exclusively.

### Lesson 4: Region Matters for 3.1
`gemini-3.1-flash-tts-preview` only works on the `global` endpoint. Using `us-texttospeech.googleapis.com` will fail.

### Lesson 5: Cloud TTS Outputs Native MP3
The entire PCM→WAV→ffmpeg→MP3 pipeline in the original script is unnecessary. Cloud TTS API can output MP3 directly via `audio_encoding=AudioEncoding.MP3`.

---

## 9. GCP Project Reference

| Property | Value |
|----------|-------|
| Project Name | Gemini API |
| Project ID | `gen-lang-client-0229532959` |
| Project Number | `399185272287` |
| Enabled APIs | Vertex AI API, Cloud Text-to-Speech API |
| Auth account | `kevinjdragan@gmail.com` |
| Service account | TBD (to be created for VPS) |
| Infisical key (SA) | `GCP_TTS_SERVICE_ACCOUNT_KEY` |
