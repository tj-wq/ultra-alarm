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
- **Training plan source (primary)**: UltraRun.Club MCP server — `https://mcp.ultrarun.club`
  - Connected via Anthropic API's `mcp_servers` parameter
  - Gives Claude direct access to: upcoming workouts, recent Strava activities, activity stats, weekly summaries, training preferences, goal races, and the ability to update/modify workouts
- **Training plan source (fallback)**: UltraRun.Club iCal feed (public URL, no auth):
  `https://api.ultrarun.club/api/calendar/feed/e494e007fca549ee82d344b51e770935.ics`
  - Used when MCP is unavailable or for the simple alarm_clock.py one-shot mode
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
- `mcp_server_url` — UltraRun.Club MCP server: `https://mcp.ultrarun.club`
- `ical_url` — the UltraRun.Club feed URL (fallback data source)
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
- `use_mcp` — boolean, default true. Set to false to use iCal-only mode

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

**LLM (Conversation) — MCP-Enhanced:**
- Anthropic Messages API (`/v1/messages`) with UltraRun.Club MCP server attached
- Every API call includes the MCP server so Claude can fetch live training data:
  ```python
  resp = httpx.post("https://api.anthropic.com/v1/messages", json={
      "model": config["model"],
      "max_tokens": 1024,  # higher to accommodate tool use overhead
      "system": system_prompt,
      "messages": conversation_history,
      "mcp_servers": [{
          "type": "url",
          "url": "https://mcp.ultrarun.club",
          "name": "ultrarun-club"
      }]
  })
  ```
- **What MCP gives the coach that iCal can't:**
  - `get_upcoming_workouts` — today's and this week's full plan with workout IDs
  - `get_recent_activities` — actual Strava data: what I really ran, pace, HR, distance
  - `get_activity_stats` — weekly mileage, all-time totals, trend data
  - `get_weekly_summary` — completed vs planned, distance breakdowns
  - `get_activity_splits` — mile-by-mile splits from recent runs (pace drift, HR zones)
  - `get_goal_race` — race calendar with target times and dates
  - `get_strength_workouts` — recent strength sessions
  - `update_workout` — **the coach can actually modify today's workout** if I say "cut it to 8 miles" or "swap to a rest day"
  - `mark_workout_complete` — mark a workout done with notes
- Maintain full conversation history (multi-turn) within a session
- System prompt includes:
  - Coach persona/personality (see Rocky Persona section below)
  - Current date/time
  - Work start time
  - Mode context (morning vs evening)
  - Instruction to use MCP tools to fetch workout/training context rather than relying on stale data
  - Instruction to keep spoken responses short (2-4 sentences) since this is voice
  - No markdown, no bullet points, no special characters — voice-friendly plain text
  - Instruction that when using tools, do NOT narrate tool calls — just speak the result naturally
- `max_tokens: 1024` (higher than before because MCP tool calls consume tokens in the response)
- **Response parsing**: MCP responses include `mcp_tool_use` and `mcp_tool_result` content blocks alongside `text` blocks. Extract only the `text` blocks for TTS:
  ```python
  text_parts = [block["text"] for block in data["content"] if block["type"] == "text"]
  spoken_response = " ".join(text_parts)
  ```
- Graceful fallback if MCP is unreachable: fall back to iCal-based context (fetch feed, parse workout, inject into system prompt as static text)
- Graceful fallback if entire API is unreachable: simple static message with workout info from iCal

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

Example morning greeting (MCP-enhanced — coach pulled this week's data):
"Good morning friend! Today is twelve miles, easy effort. You ran thirty-eight miles so far this week — good volume. Your easy pace has been creeping to ten forty-five though. Today, keep heart rate below one forty. Slow legs, quiet brain. Is good day for running, yes yes yes."

Example response to "legs are heavy" (coach can check recent activity):
"Hmm, understand. I see yesterday was six miles at ten twenty pace — that is not easy pace, friend. Your body is telling you something. Start first two miles very slow. If legs still heavy at mile three, I cut workout to eight miles. Want me to update the plan, question?"

Example response to "yeah cut it to 8" (coach updates the workout via MCP):
"Done. I update today to eight miles easy. No shame — smart runner is alive runner. You have Canyons in twenty-six days. We protect the legs now so they work on race day."

Example evening confirmation:
"Tomorrow workout: twelve miles, easy pace. Your weekly total is thirty-one miles — on track for taper. I calculate alarm at five fifteen to give time for run plus shower plus food before work. This is good plan, question?"

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

**Layer 2: Fine-tuned Piper Model via TextyMcSpeechy (requires training on GPU)**

TextyMcSpeechy (https://github.com/domesticatedviking/TextyMcSpeechy) is the recommended pipeline. It combines Piper TTS with Applio voice conversion — you can train Piper to mimic a target voice without needing the target's original recordings as the training dataset. The workflow:

1. **Get Rocky source audio** — Extract clips from trailers, featurettes, interviews where Rocky's translated voice is audible. Even 3-5 minutes of clean audio is enough for an RVC/Applio voice model.
2. **Train an RVC model from Rocky clips** — This captures Rocky's vocal timbre/character.
3. **Use Applio to convert a standard Piper dataset** — Take the existing `en_US-lessac-medium` dataset and voice-convert it to sound like Rocky using the RVC model. This gives you ~13,000 phrases in Rocky's voice.
4. **Fine-tune Piper on the converted dataset** — Standard Piper training, but now with a massive dataset in Rocky's voice.
5. **Export to ONNX and deploy to Pi** — The model runs on CPU, no GPU needed for inference.

#### TextyMcSpeechy Setup (`voice_training/setup_textymcspeechy.sh`)
```bash
git clone https://github.com/domesticatedviking/TextyMcSpeechy.git
cd TextyMcSpeechy
# Follow setup instructions — requires Python 3.10+, GPU for training
# Install Applio for voice conversion
# Install Piper training dependencies
```

#### Dataset Preparation (`voice_training/prepare_dataset.py`)
- Accept a directory of source audio clips (Rocky's translated voice from movie trailers/featurettes)
- Clean up audio: normalize volume, trim silence, remove background music/effects where possible
- Split long clips into individual utterances (split on silence gaps)
- Transcribe each clip using Whisper to generate text labels
- Output in format suitable for RVC training (clean mono WAV, 44.1kHz)

#### RVC Model Training
- Use Applio (bundled with TextyMcSpeechy) to train an RVC v2 model from the Rocky clips
- ~3-5 minutes of clean audio, 200-500 epochs
- This captures Rocky's vocal quality — the slightly flat, mechanical, earnest tone

#### Voice Conversion of Piper Dataset
- Take the standard `en_US-lessac-medium` LJSpeech dataset (~13,000 phrases)
- Run each phrase through Applio voice conversion using the Rocky RVC model
- Output: ~13,000 phrases that sound like Rocky, with matching text transcripts
- Apply the sox post-processing filter (Layer 1) to add the "translation computer" quality

#### Piper Fine-Tuning (`voice_training/train.sh`)
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

#### Alternative: Synthetic Dataset (if not enough Rocky source audio)
If clean Rocky audio is scarce (movie just came out March 20, 2026), generate a synthetic dataset:

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
├── alarm_clock.py                     # Simple one-shot alarm (no mic needed, uses iCal)
├── coach.py                           # Conversational voice coach (main interface, uses MCP)
├── oauth_setup.py                     # One-time OAuth flow to get MCP auth token (Option B)
├── rocky_filter.sh                    # Sox/ffmpeg audio post-processing for Rocky voice
│
├── voice_training/
│   ├── README.md                      # Training-specific docs
│   ├── setup_textymcspeechy.sh        # TextyMcSpeechy + Applio + Piper training env setup
│   ├── setup_rocm.sh                  # AMD ROCm setup for 9070 XT
│   ├── prepare_dataset.py             # Rocky audio clips → clean utterances for RVC
│   ├── generate_rocky_phrases.py      # Claude API → synthetic phrase dataset (fallback)
│   ├── convert_dataset.sh             # Applio voice conversion: lessac dataset → Rocky voice
│   ├── train_rvc.sh                   # Train RVC v2 model from Rocky clips
│   ├── train_piper.sh                 # Fine-tune Piper on converted dataset
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

1. **MCP as primary data source, iCal as fallback**: The UltraRun.Club MCP server (`https://mcp.ultrarun.club`) is passed to every Claude API call via the `mcp_servers` parameter. This lets Claude fetch live training data — upcoming workouts, recent Strava activities, weekly stats, mile splits — and even *modify* workouts during the conversation. The iCal feed remains as a lightweight fallback for the simple `alarm_clock.py` one-shot mode and for cases where MCP is unreachable. The iCal feed URL is: `https://api.ultrarun.club/api/calendar/feed/e494e007fca549ee82d344b51e770935.ics`

2. **MCP tool calls are invisible to the user**: When Claude uses MCP tools during a conversation, the API response contains `mcp_tool_use` and `mcp_tool_result` content blocks alongside `text` blocks. Only the `text` blocks should be sent to TTS. The system prompt must instruct Claude not to narrate its tool usage — it should just speak naturally with the data it retrieved. Example: DON'T say "Let me check your recent activities..." → DO say "You ran thirty-eight miles this week and your easy pace has been averaging ten forty-five."

3. **MCP enables live plan modifications**: If I say "cut today to 8 miles" or "swap tomorrow to a rest day," the coach can call `update_workout` via MCP to actually make the change in my training plan. This is a killer feature — the alarm clock isn't just reading the plan, it's an active coaching interface. The coach should confirm before making changes: "I update today to eight miles. Is good, question?"

4. **Whisper local over cloud STT**: Privacy, no ongoing costs, works offline after model download. `base.en` is the sweet spot on Pi 4/5 (~3s latency).

5. **Piper over cloud TTS**: Fast, local, free. The Rocky voice model deploys as a single .onnx file. espeak as fallback is always available.

6. **`at` command over cron for dynamic scheduling**: Alarm time changes daily based on workout distance. `at` lets us schedule a one-shot job at a specific time. Cron is for the fixed nightly trigger.

7. **Conversation history in-memory only**: No persistence between sessions. Each morning/evening is a fresh conversation with workout context fetched live via MCP.

8. **Sox post-processing as immediate win**: Gets the Rocky "translation computer" voice quality without any model training. Layer the fine-tuned model on top later for the full effect.

9. **Synthetic dataset as training data**: I may not be able to get enough clean Rocky audio from the movie. Generating 1300 phrases in Rocky's speech patterns via Claude, then synthesizing with a filtered Piper voice, creates a viable training dataset that captures the cadence even if the exact timbre is approximate.

---

## UltraRun.Club MCP Server — Available Tools Reference

The MCP server at `https://mcp.ultrarun.club` exposes the following tools. When passed via `mcp_servers` in the API call, Claude can invoke these automatically during conversation.

**Read-only tools (safe to call freely):**
- `get_upcoming_workouts(days?)` — upcoming planned workouts from active plan. Returns workout IDs, dates, summaries, descriptions, distances, intensities. Default 7 days.
- `get_recent_activities(days?)` — recent Strava activities with distance, pace, duration, HR.
- `get_activity_stats()` — this week's metrics and all-time totals.
- `get_weekly_summary()` — comprehensive weekly summary: completed activities, distances, times, breakdowns.
- `get_activity_splits(activity_id)` — mile-by-mile splits for a specific activity including pace, HR, elevation.
- `get_goal_race()` — all races from the race calendar with dates, distances, target times.
- `get_strength_workouts()` — recent strength training sessions with exercises, sets, reps.
- `get_training_plans()` — all training plans with status, dates, workout counts.
- `get_training_preferences()` — distance unit, long run days, other settings.
- `get_calendar_url()` — iCal subscription URL.
- `get_change_log()` — history of AI coach changes to the active plan.

**Write tools (coach should confirm before using):**
- `update_workout(workout_id, ...)` — modify a planned workout's distance, description, type, duration, intensity. Requires `workout_id` from `get_upcoming_workouts`. Always provide `change_reason`.
- `mark_workout_complete(workout_id, notes?)` — mark a workout as completed with optional notes.
- `create_workout(plan_id, start_date, subject, ...)` — add a new workout to a plan.
- `delete_workout(workout_id)` — delete a planned workout.

**System prompt guidance for MCP tool use:**
The system prompt should instruct Claude to:
1. On morning mode startup: call `get_upcoming_workouts(days=1)` and `get_activity_stats()` to get today's workout and weekly context
2. If I mention how I'm feeling or ask about recent training: call `get_recent_activities()` or `get_weekly_summary()`
3. If I ask to modify a workout: call `update_workout()` but confirm the change verbally first
4. Never narrate tool calls in spoken output — just use the data naturally
5. If MCP tools fail, fall back to the information in the system prompt and mention that live data wasn't available

---

## MCP Authentication

The Anthropic API MCP connector passes an `authorization_token` as a Bearer token to the MCP server. The Pi needs a valid token to authenticate.

### Option A: Personal API Token (Recommended — requires UltraRun.Club feature)
If UltraRun.Club supports generating a personal API token from the website:
1. Log into ultrarun.club → Settings → API → Generate Token
2. Copy the token into `config.json` as `mcp_auth_token`
3. The Pi passes it on every API call:
   ```python
   "mcp_servers": [{
       "type": "url",
       "url": "https://mcp.ultrarun.club",
       "name": "ultrarun-club",
       "authorization_token": config["mcp_auth_token"]
   }]
   ```
4. Token is long-lived, no refresh needed. Revocable from the website.

This is the simplest path. If building this feature on the UltraRun.Club side:
- Add a "Personal API Tokens" section to the user settings page
- Token generation: create a long-lived bearer token tied to the user account
- Token should have the same permissions as the OAuth session (read/write workouts, read activities)
- Store hashed, display once on creation, allow revocation
- The MCP server validates incoming `Authorization: Bearer <token>` headers against stored tokens

### Option B: One-Time OAuth Flow + Token Storage
If UltraRun.Club only supports OAuth (Strava-style browser flow):
1. Run a one-time setup script on any machine with a browser:
   ```bash
   python3 oauth_setup.py  # Opens browser, completes OAuth, saves tokens
   ```
2. Script captures `access_token` and `refresh_token`, writes to `config.json`
3. The Pi uses `access_token` as `authorization_token` in API calls
4. A background refresh mechanism checks token expiry and uses `refresh_token` to get a new `access_token` before it expires
5. Complexity: need to handle token refresh, expiry, and storage securely

### Configuration
Add to `config.json`:
```json
{
  "mcp_auth_token": "ultrarun_pat_...",  // Option A: personal API token
  // OR for Option B:
  "mcp_oauth_access_token": "...",
  "mcp_oauth_refresh_token": "...",
  "mcp_oauth_expires_at": "2026-06-01T00:00:00Z"
}
```

### API Call Structure (with auth + beta header)
```python
resp = httpx.post(
    "https://api.anthropic.com/v1/messages",
    headers={
        "x-api-key": config["anthropic_api_key"],
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "mcp-client-2025-11-20",
    },
    json={
        "model": config["model"],
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": conversation_history,
        "mcp_servers": [{
            "type": "url",
            "url": config["mcp_server_url"],
            "name": "ultrarun-club",
            "authorization_token": config["mcp_auth_token"],
        }],
        "tools": [{
            "type": "mcp_toolset",
            "mcp_server_name": "ultrarun-club",
        }],
    },
    timeout=30,
)
```

Note the `anthropic-beta: mcp-client-2025-11-20` header — this is required for the MCP connector feature. The `tools` array with `mcp_toolset` tells Claude which MCP server's tools to enable.

---

- [ ] `alarm_clock.py preview` shows correct workout and alarm time (iCal fallback)
- [ ] `alarm_clock.py alarm` speaks a coaching message through the speaker
- [ ] `coach.py test-voice` records from mic, transcribes, responds, speaks
- [ ] `coach.py morning` enters a multi-turn voice conversation with MCP data
- [ ] `coach.py evening` confirms tomorrow's workout and schedules alarm via `at`
- [ ] MCP integration: Claude fetches upcoming workouts via MCP during conversation
- [ ] MCP integration: Claude fetches weekly stats and references them in coaching
- [ ] MCP integration: "cut today to 8 miles" triggers `update_workout` and confirms
- [ ] MCP fallback: conversation works with iCal when MCP is unreachable
- [ ] API response parsing: only `text` blocks are sent to TTS (tool blocks filtered out)
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
- **MCP integration is the highest-value feature.** The coach being able to pull live Strava data and modify workouts is what makes this more than just a fancy alarm. Get this working first, then layer on the Rocky voice.
- The voice training pipeline is Phase 2 — spec it out fully but it's okay if the scripts are less battle-tested.
- All Python should be 3.11+ (match Pi OS default). Use `from __future__ import annotations` if needed.
- Type hints everywhere. Docstrings on public functions.
- No classes where a function will do. The `CoachConversation` class is the exception — it needs to hold state.
- Config should be a single flat JSON file, not YAML, not TOML, not env vars (except API key which should support both).
- Error handling: the alarm must fire even if the API is down. Always have a fallback message. MCP failure should degrade gracefully to iCal mode, not crash.
- **MCP response parsing is critical**: The API response `data["content"]` is a list of blocks with different `type` values. Only extract `type: "text"` blocks for TTS output. `mcp_tool_use` and `mcp_tool_result` blocks are internal and must not be spoken. Filter by type, not by position.
- Audio pipeline: record → temp wav → transcribe → delete temp. TTS: generate → temp wav → post-process → play → delete temp. Don't leave temp files around.
- The `rocky_filter.sh` sox command chain is crucial — spend time making it sound good. Provide 3 presets.
- For the synthetic dataset generator, batch the Claude API calls sensibly (don't make 1300 individual calls — batch by category with a single prompt that generates 20-30 phrases at once).
- Test on x86 Linux first (everything except PyAudio recording), then deploy to Pi.
- **MCP latency consideration**: MCP tool calls add ~2-5 seconds to the first response as Claude fetches data. This is acceptable for the opening greeting but might feel slow in rapid back-and-forth. For follow-up turns where Claude already has context from earlier tool calls, it may not need to call tools again — the system prompt should note this.
