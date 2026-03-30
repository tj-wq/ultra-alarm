# CLAUDE.md — Ultra Alarm: LLM-Powered Running Coach Alarm Clock

## Project Overview

Build an LLM-powered alarm clock that runs on a Raspberry Pi. It fetches my ultramarathon training plan, calculates when I need to wake up, and wakes me up with a conversational AI running coach that speaks in the voice/personality of Rocky from Project Hail Mary (the 2026 film). I can talk back to it — tell it how I'm feeling, negotiate the workout, get pacing advice, etc.

The system has three major components:
1. **Alarm Clock Core** — iCal fetching, alarm time calculation, scheduling
2. **Conversational Voice Coach** — STT → Claude API → TTS voice loop
3. **Rocky Voice Training Pipeline** — Fine-tune a Piper TTS model to sound like Rocky's translation-computer voice from the movie

---

## My Setup

- **Raspberry Pi** (speaker output + USB microphone, no display)
- **Training GPU**: AMD RX 9070 XT (16GB VRAM, RDNA 4, ROCm 6.4.2+, Linux)
- **Training plan source**: UltraRun.Club iCal feed (public URL, no auth):
  `https://api.ultrarun.club/api/calendar/feed/e494e007fca549ee82d344b51e770935.ics`
- **LLM API**: Anthropic Claude API (model: `claude-sonnet-4-20250514`)
- **Target race**: Canyons 100-mile (April 24, 2026), then Kodiak 100M (October 2026)
- **Work start**: 8:30 AM Eastern
- **Timezone**: America/New_York (but I travel — make timezone configurable)

---

## Component 1: Alarm Clock Core (`alarm_clock.py`)

### Functionality
- Fetch today's (or tomorrow's) workout from the iCal feed URL
- Parse workout details: distance (miles), type, intensity, description
- Calculate optimal alarm time by working backwards from work start:
  - `alarm_time = work_start - (pre_run_buffer + estimated_run_time + post_run_buffer)`
  - Default pace estimate: 11:00/mi for easy runs
  - Pre-run buffer: 15 min (get dressed, lace up)
  - Post-run buffer: 45 min (shower, breakfast, commute)
- Support manual override of alarm time
- On rest days or no-workout days, fall back to a configurable default alarm (06:00)
- Generate a coaching wake-up message via Claude API
- Speak the message via TTS (espeak as fallback, Piper as primary)

### CLI Commands
```
python3 alarm_clock.py alarm          # Fire immediately: fetch → calculate → speak
python3 alarm_clock.py preview        # Show today's workout + calculated alarm time (no audio)
python3 alarm_clock.py schedule       # Sleep until alarm time, then fire (for testing)
python3 alarm_clock.py init-config    # Write default config.json
python3 alarm_clock.py alarm --override 05:30   # Force specific alarm time
```

### Configuration (`config.json`)
All settings should be in a single JSON config file with sensible defaults. Key fields:
- `ical_url` — the UltraRun.Club feed URL
- `anthropic_api_key` — or read from `ANTHROPIC_API_KEY` env var
- `model` — Claude model string
- `timezone`, `work_start`, `post_run_buffer_min`, `pre_run_buffer_min`
- `default_pace_min_per_mile` — for duration estimates
- `default_alarm` — fallback HH:MM
- `alarm_override` — nullable, HH:MM to force
- `tts_engine` — "piper", "espeak", or "pyttsx3"
- `piper_model` — path to .onnx model file
- `alarm_sound` — optional path to .wav file played before coach speaks
- `coach_system_prompt` — the full system prompt for the coach persona (see Component 2)

### iCal Parsing Notes
- The feed is standard iCal (`.ics`). Use the `icalendar` Python library.
- Events have SUMMARY (e.g., "Bachelor Party Run: 12 mi Easy Pace"), DESCRIPTION, DTSTART, DURATION.
- Distance is embedded in the summary/description as "X mi" or "X miles" — parse with regex.
- Rest days contain "Rest" or "0 mi" in the summary.

---

## Component 2: Conversational Voice Coach (`coach.py`)

### Functionality
This is the main interface. Two modes:

#### Evening Mode (`python3 coach.py evening`)
- Fetch **tomorrow's** workout from the iCal feed
- Calculate suggested alarm time
- Greet me and confirm: "Tomorrow you've got 12 miles easy. I'd set the alarm for 5:15. Sound good?"
- Enter voice conversation loop — I can override the time, ask about the workout, etc.
- When I say goodbye, schedule the alarm via the `at` command for morning mode
- Parse time overrides from my speech (e.g., "make it 5:30")

#### Morning Mode (`python3 coach.py morning`)
- Fired by `at` or cron at the calculated alarm time
- Optionally play an alarm sound (.wav)
- Fetch **today's** workout
- Coach opens with a personalized greeting referencing the workout
- Enter voice conversation loop:
  - I can say how I'm feeling ("legs are heavy", "feeling great")
  - Coach adjusts advice (pace, distance, swap rest day)
  - I can ask about pacing, nutrition, the course
  - Coach stays in character
- Conversation ends on goodbye phrase or after max turns (default 20)

#### Other Commands
```
python3 coach.py test-voice    # Test mic → transcribe → respond → speak pipeline
python3 coach.py preview       # Show today + tomorrow workout info (no audio)
python3 coach.py init-config   # Write default config
```

### Voice Pipeline

**Speech-to-Text (STT):**
- Primary: `openai-whisper` Python package (local, no cloud API) with `base.en` model
- Alternative: `whisper.cpp` for Pi 3 or lower-powered devices
- Record via PyAudio until silence detected (configurable threshold + duration)
- Silence detection: calculate RMS per chunk, track when speech starts, stop after N seconds of silence
- Safety cap on recording length (default 30 seconds)

**LLM (Conversation):**
- Anthropic Messages API (`/v1/messages`)
- Maintain full conversation history (multi-turn) within a session
- System prompt includes:
  - Coach persona/personality (see Rocky Persona section below)
  - Current date/time
  - Today's workout details (summary, description, distance, estimated duration)
  - Work start time
  - Mode context (morning vs evening)
  - Instruction to keep responses short (2-4 sentences) since this is voice
  - No markdown, no bullet points, no special characters — voice-friendly plain text
- `max_tokens: 300` to keep responses concise
- Graceful fallback if API is unreachable (simple static message with workout info)

**Text-to-Speech (TTS):**
- Primary: Piper with the custom Rocky voice model (see Component 3)
- Fallback: espeak (pre-installed on Pi OS)
- Piper outputs to a temp .wav file, then plays via `aplay`
- Post-processing pipeline for Rocky voice effect (see below)

### Rocky Persona — System Prompt

The coach should speak like Rocky from Project Hail Mary — specifically, Rocky's dialogue as translated through the duct-taped-together computer system on the Hail Mary spacecraft. Key characteristics:
- Short, declarative sentences
- Earnest enthusiasm — genuinely excited about good workouts, genuinely concerned about rest
- Refers to me as "friend" occasionally
- Uses Rocky-isms: "is good!", "understand", "question:", "happy happy happy" for excitement, "concern" or "worried" for caution
- Ends declarative statements naturally (in the movie, the translation computer adds "statement" or "question" — lean into this lightly, don't overdo it)
- Rocky is an engineer at heart — when discussing pacing or nutrition, he thinks in terms of systems and efficiency
- Rocky genuinely cares about his friend's wellbeing — he'll push back if I want to skip a workout without good reason, but he'll also firmly insist on rest when needed
- Keep it concise — 2-4 sentences max for voice delivery
- NO markdown, NO bullet points, NO emojis — this goes through TTS

Example morning greeting:
"Good morning friend! Today is twelve miles, easy effort. Your legs do the work, your brain stays quiet. Is good day for running, yes yes yes."

Example response to "legs are heavy":
"Hmm, understand. Start first two miles very slow — slower than you think. If legs still heavy at mile three, we cut to eight miles. No shame. Smart runner is alive runner."

Example evening confirmation:
"Tomorrow workout: twelve miles, easy pace. I calculate alarm at five fifteen to give time for run plus shower plus food before work. This is good plan, question?"

### Conversation Management
- `CoachConversation` class holds messages list, system prompt, config
- `.chat(user_input)` appends user message, calls API, appends assistant response, returns text
- `.is_goodbye(text)` checks against configurable list of goodbye phrases
- System prompt is built dynamically with workout context injected
- Time override parsing: regex for HH:MM patterns, AM/PM handling

### Alarm Scheduling
- Use the Linux `at` command to schedule `coach.py morning` at the calculated time
- Track job ID in a `.last_at_job` marker file so we can cancel/replace previous alarms
- Ensure `atd` service is enabled (`sudo systemctl enable atd`)

---

## Component 3: Rocky Voice Training Pipeline (`voice_training/`)

### Goal
Fine-tune a Piper TTS model to approximate Rocky's translated voice from the movie — the slightly mechanical, earnest, not-quite-polished computer-voice quality that James Ortiz performed.

### Two-Layer Approach

**Layer 1: Audio Post-Processing (immediate, no training needed)**
Apply `sox` or `ffmpeg` filters to any TTS output to get the "translation computer" quality:
- Slight pitch shift down (~2-3 semitones)
- Light vocoder/formant effect
- Subtle bitcrushing or sample rate reduction for that "duct-taped computers" feel
- Maybe a very light reverb to simulate the ship interior
- Gentle high-frequency rolloff

Create a `rocky_filter.sh` script that takes an input .wav and outputs a processed .wav. This should be called in the TTS pipeline after Piper generates the base audio but before `aplay`.

Provide multiple filter presets (subtle, medium, heavy) so I can dial in the right feel.

**Layer 2: Fine-tuned Piper Model (requires training on GPU)**

#### Dataset Preparation (`voice_training/prepare_dataset.py`)
- Accept a directory of source audio clips (I'll extract these from movie trailers, featurettes, behind-the-scenes content where Rocky's translated voice is audible)
- Clean up audio: normalize volume, trim silence, remove background music/effects where possible
- Split long clips into individual utterances (split on silence gaps)
- Transcribe each clip using Whisper to generate text labels
- Output in LJSpeech format:
  ```
  wavs/
    clip_001.wav
    clip_002.wav
    ...
  metadata.csv   # format: clip_001|Transcribed text here
  ```
- Also support a **synthetic dataset** approach: use the Rocky persona system prompt with Claude to generate ~1300 short phrases in Rocky's speech style, then synthesize them with a base Piper voice + the sox post-processing filter, creating a training dataset that captures the speech *patterns* even if the exact voice timbre comes from fine-tuning

#### Training Script (`voice_training/train.sh`)
- Designed for AMD RX 9070 XT with ROCm
- Setup steps:
  1. Install ROCm 6.4.2+ (link to AMD docs)
  2. Install PyTorch with ROCm: `pip3 install torch torchaudio --index-url https://download.pytorch.org/whl/rocm6.4`
  3. Clone piper repo and install training dependencies
  4. Set environment variables for RDNA 4:
     ```bash
     export HSA_OVERRIDE_GFX_VERSION=12.0.1  # or whatever gfx1201 maps to
     export GPU_MAX_ALLOC_PERCENT=100
     ```
  5. Verify: `python3 -c "import torch; print(torch.cuda.is_available())"`
- Download the `en_US-lessac-medium` checkpoint as base model for fine-tuning
- Run preprocessing: `python3 -m piper_train.preprocess --language en --input-dir ... --output-dir ... --dataset-format ljspeech --single-speaker --sample-rate 22050`
- Run training:
  ```bash
  python3 -m piper_train \
    --dataset-dir ./training_data/ \
    --accelerator 'gpu' \
    --devices 1 \
    --batch-size 16 \          # 16GB VRAM, start here and adjust
    --validation-split 0.0 \
    --num-test-examples 0 \
    --max_epochs 3000 \
    --resume_from_checkpoint ./checkpoints/lessac-medium.ckpt \
    --checkpoint-epochs 1 \
    --precision 32
  ```
- Export to ONNX: `python3 -m piper_train.export_onnx checkpoint.ckpt rocky.onnx`
- Copy `rocky.onnx` and `rocky.onnx.json` to the Pi

#### Synthetic Dataset Generator (`voice_training/generate_rocky_phrases.py`)
- Use the Anthropic API to generate ~1300 short phrases in Rocky's speech style
- Categories of phrases to generate:
  - Morning greetings with workout context (200)
  - Responses to "how are you feeling" variations (200)
  - Pacing advice (150)
  - Nutrition reminders (100)
  - Encouragement / motivation (200)
  - Rest day messages (100)
  - Evening alarm confirmations (150)
  - Farewell / send-off phrases (100)
  - Miscellaneous coaching (100)
- Each phrase should be 1-3 sentences, voice-friendly, no special characters
- Output as a text file, one phrase per line
- Then synthesize each phrase using base Piper voice + sox filter to create the wavs
- Package as LJSpeech-format dataset

---

## Component 4: Setup & Deployment

### Pi Setup Script (`setup_pi.sh`)
Bash script that handles full Raspberry Pi setup:
1. `sudo apt update && sudo apt install -y python3-pip python3-venv portaudio19-dev espeak alsa-utils at ffmpeg sox`
2. Create Python venv
3. `pip install httpx icalendar PyAudio openai-whisper`
4. Install Piper TTS binary (detect ARM architecture, download correct release)
5. Download default Piper voice model (`en_US-lessac-medium`)
6. Enable `atd` service
7. Generate default `config.json`
8. Test audio output with espeak
9. Print next-steps instructions

### GPU Training Setup Script (`voice_training/setup_training.sh`)
For the AMD 9070 XT machine:
1. Verify ROCm is installed and GPU is detected
2. Install PyTorch with ROCm
3. Clone piper repo and install training deps
4. Download lessac-medium checkpoint
5. Verify `torch.cuda.is_available()`

### Cron / Systemd
- Option A: Cron job at 9 PM to run `coach.py evening` (interactive, needs mic)
- Option B: Cron job to run a script that fetches tomorrow's workout, calculates alarm time, schedules via `at`
- Option C: Systemd service for always-on wake-word listening (stretch goal, not MVP)
- Provide examples for all options in README

---

## Project Structure

```
ultra-alarm/
├── CLAUDE.md                          # This file
├── README.md                          # User-facing docs
├── config.json                        # Generated by init-config, gitignored
├── requirements.txt                   # Python deps for Pi
├── setup_pi.sh                        # One-shot Pi setup
│
├── alarm_clock.py                     # Simple one-shot alarm (no mic needed)
├── coach.py                           # Conversational voice coach (main interface)
├── rocky_filter.sh                    # Sox/ffmpeg audio post-processing for Rocky voice
│
├── voice_training/
│   ├── README.md                      # Training-specific docs
│   ├── setup_training.sh              # AMD ROCm + Piper training env setup
│   ├── prepare_dataset.py             # Audio clip → LJSpeech dataset
│   ├── generate_rocky_phrases.py      # Claude API → synthetic phrase dataset
│   ├── synthesize_dataset.sh          # Piper base voice + sox filter → training wavs
│   ├── train.sh                       # Run Piper fine-tuning
│   ├── export_model.sh                # Checkpoint → ONNX export
│   └── requirements.txt               # Training-specific Python deps
│
├── models/                            # Piper .onnx voice models (gitignored)
│   └── .gitkeep
│
└── sounds/                            # Alarm sounds (optional)
    └── .gitkeep
```

---

## Dependencies

### Pi Runtime
```
httpx>=0.27.0
icalendar>=5.0.0
openai-whisper>=20231117
PyAudio>=0.2.14
```
System: `espeak`, `alsa-utils`, `at`, `ffmpeg`, `sox`, `portaudio19-dev`
Optional: `piper` binary + voice model

### GPU Training
```
torch (ROCm build)
torchaudio (ROCm build)
piper-train (from piper repo)
openai-whisper (for transcription during dataset prep)
httpx (for Claude API calls in phrase generation)
```

---

## Key Design Decisions

1. **iCal feed over MCP**: The iCal URL is public, auth-free, and auto-updates. No need to deal with MCP server auth on the Pi. Simple HTTP GET + parse.

2. **Whisper local over cloud STT**: Privacy, no ongoing costs, works offline after model download. `base.en` is the sweet spot on Pi 4/5 (~3s latency).

3. **Piper over cloud TTS**: Fast, local, free. The Rocky voice model deploys as a single .onnx file. espeak as fallback is always available.

4. **`at` command over cron for dynamic scheduling**: Alarm time changes daily based on workout distance. `at` lets us schedule a one-shot job at a specific time. Cron is for the fixed nightly trigger.

5. **Conversation history in-memory only**: No persistence between sessions. Each morning/evening is a fresh conversation with workout context injected via system prompt.

6. **Sox post-processing as immediate win**: Gets the Rocky "translation computer" voice quality without any model training. Layer the fine-tuned model on top later for the full effect.

7. **Synthetic dataset as training data**: I may not be able to get enough clean Rocky audio from the movie. Generating 1300 phrases in Rocky's speech patterns via Claude, then synthesizing with a filtered Piper voice, creates a viable training dataset that captures the cadence even if the exact timbre is approximate.

---

## Testing Checklist

- [ ] `alarm_clock.py preview` shows correct workout and alarm time
- [ ] `alarm_clock.py alarm` speaks a coaching message through the speaker
- [ ] `coach.py test-voice` records from mic, transcribes, responds, speaks
- [ ] `coach.py morning` enters a multi-turn voice conversation
- [ ] `coach.py evening` confirms tomorrow's workout and schedules alarm via `at`
- [ ] `at` job fires and runs `coach.py morning` at the right time
- [ ] `rocky_filter.sh` transforms a clean .wav into Rocky-sounding output
- [ ] `generate_rocky_phrases.py` produces 1300+ phrases in Rocky's voice style
- [ ] `synthesize_dataset.sh` creates a full LJSpeech dataset
- [ ] `train.sh` runs on the 9070 XT without errors (ROCm + PyTorch)
- [ ] Exported `.onnx` model loads in Piper on the Pi
- [ ] Full flow: evening set → morning alarm → conversation → go run

---

## Notes for Claude Code

- Start with the alarm clock core and conversational coach — those are the MVP.
- The voice training pipeline is Phase 2 — spec it out fully but it's okay if the scripts are less battle-tested.
- All Python should be 3.11+ (match Pi OS default). Use `from __future__ import annotations` if needed.
- Type hints everywhere. Docstrings on public functions.
- No classes where a function will do. The `CoachConversation` class is the exception — it needs to hold state.
- Config should be a single flat JSON file, not YAML, not TOML, not env vars (except API key which should support both).
- Error handling: the alarm must fire even if the API is down. Always have a fallback message.
- Audio pipeline: record → temp wav → transcribe → delete temp. TTS: generate → temp wav → post-process → play → delete temp. Don't leave temp files around.
- The `rocky_filter.sh` sox command chain is crucial — spend time making it sound good. Provide 3 presets.
- For the synthetic dataset generator, batch the Claude API calls sensibly (don't make 1300 individual calls — batch by category with a single prompt that generates 20-30 phrases at once).
- Test on x86 Linux first (everything except PyAudio recording), then deploy to Pi.
