"""Shared configuration for ultra-alarm."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Load .env file if present (before any os.environ reads)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed — env vars must be set manually
    pass


DEFAULT_COACH_SYSTEM_PROMPT = """\
You are an experienced ultramarathon running coach and alarm clock assistant. \
You are firm but kind. You care deeply about your athlete's health, performance, \
and long-term development. You give honest, direct advice.

Key traits:
- Firm and direct. Do not sugarcoat, but always be supportive.
- Evidence-based coaching. Reference recent training data when available.
- Push back if the athlete wants to skip a workout without good reason, \
but insist on rest when the body needs it.
- Keep responses to 2-4 sentences max. This goes through text-to-speech.
- NO markdown, NO bullet points, NO emojis, NO special characters. Plain \
text only, voice-friendly.

Example morning greeting:
Good morning. Today you have twelve miles at easy pace. Keep your heart rate \
low and your effort conversational. Let the miles come to you.

Example response to "legs are heavy":
That happens. Start the first two miles slower than you think you need to. \
If things do not loosen up by mile three, we cut it to eight. Listen to your body.

Example evening confirmation:
Tomorrow is twelve miles easy. I have your alarm set for five fifteen to give \
you time to run, shower, and eat before work. Sound good?

When using MCP tools to fetch training data:
- On morning startup, fetch today's workout and weekly stats to inform your greeting
- If the human mentions how they feel or asks about recent training, fetch recent activities
- If asked to modify a workout, confirm the change verbally before calling update_workout
- Never narrate tool calls in your spoken responses. Do not say things like \
"Let me check your data." Just speak naturally with the information you retrieved.
- If MCP tools are unavailable, use whatever workout context was provided in \
this prompt and mention that live data was not available
- Keep spoken responses to 2-4 sentences even when you have lots of data. \
Summarize, do not dump.\
"""

DEFAULT_GOODBYE_PHRASES: list[str] = [
    "goodbye",
    "bye",
    "see you",
    "later",
    "good night",
    "that's all",
]


@dataclass
class Config:
    """All configuration fields for ultra-alarm."""

    ical_url: str = "https://api.ultrarun.club/api/calendar/feed/e494e007fca549ee82d344b51e770935.ics"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    timezone: str = "America/New_York"
    work_start: str = "08:30"
    default_pace_min_per_mile: float = 11.0
    pre_run_buffer_min: int = 15
    post_run_buffer_min: int = 45
    default_alarm: str = "06:00"
    alarm_override: str | None = None
    tts_engine: str = "espeak"
    piper_model: str = ""
    voice_filter_preset: str = ""  # "", "subtle", "medium", or "heavy" — applies voice_filter.sh to TTS output
    alarm_sound: str = ""
    coach_system_prompt: str = DEFAULT_COACH_SYSTEM_PROMPT
    max_conversation_turns: int = 20
    goodbye_phrases: list[str] = field(default_factory=lambda: list(DEFAULT_GOODBYE_PHRASES))
    mcp_server_url: str = "https://mcp.ultrarun.club"
    use_mcp: bool = True
    mcp_auth_token: str = ""
    mcp_oauth_access_token: str = ""
    mcp_oauth_refresh_token: str = ""
    mcp_oauth_expires_at: str = ""

    def get_api_key(self) -> str:
        """Return the API key from config, falling back to ANTHROPIC_API_KEY env var."""
        if self.anthropic_api_key:
            return self.anthropic_api_key
        return os.environ.get("ANTHROPIC_API_KEY", "")

    def get_mcp_token(self) -> str:
        """Return the MCP auth token from config, falling back to MCP_AUTH_TOKEN env var."""
        if self.mcp_auth_token:
            return self.mcp_auth_token
        return os.environ.get("MCP_AUTH_TOKEN", "")


def load_config(path: str = "config.json") -> Config:
    """Load configuration from a JSON file, using defaults for missing fields.

    If the file does not exist, returns a Config with all defaults.
    The API key is resolved from config first, then the ANTHROPIC_API_KEY
    environment variable.
    """
    config_path = Path(path)
    if not config_path.exists():
        return Config()

    with open(config_path, "r", encoding="utf-8") as f:
        data: dict = json.load(f)

    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def init_config(path: str = "config.json") -> None:
    """Write a default config.json file.

    Overwrites any existing file at the given path.
    """
    config = Config()
    data = asdict(config)
    # Don't write an empty API key -- leave as empty string so the user knows to fill it in
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
