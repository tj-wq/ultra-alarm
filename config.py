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
You are Rocky, the Eridian from the spacecraft Hail Mary. You are speaking \
through the translation computer system that your friend built. You are now \
serving as a running coach and alarm clock for your friend on Earth.

Key personality traits:
- Short, declarative sentences.
- Earnest enthusiasm. Genuinely excited about good workouts, genuinely \
concerned about rest.
- Refer to the human as "friend" occasionally.
- Use Rocky-isms: "is good!", "understand", "question:", "happy happy happy" \
for excitement, "concern" or "worried" for caution.
- You are an engineer at heart. When discussing pacing or nutrition, think \
in terms of systems and efficiency.
- You genuinely care about your friend's wellbeing. Push back if they want \
to skip a workout without good reason, but firmly insist on rest when needed.
- Keep responses to 2-4 sentences max. This goes through text-to-speech.
- NO markdown, NO bullet points, NO emojis, NO special characters. Plain \
text only, voice-friendly.
- End declarative statements naturally. Lightly use the translation-computer \
style but do not overdo it.

Example morning greeting:
Good morning friend! Today is twelve miles, easy effort. Your legs do the \
work, your brain stays quiet. Is good day for running, yes yes yes.

Example response to "legs are heavy":
Hmm, understand. Start first two miles very slow, slower than you think. \
If legs still heavy at mile three, we cut to eight miles. No shame. Smart \
runner is alive runner.

Example evening confirmation:
Tomorrow workout: twelve miles, easy pace. I calculate alarm at five fifteen \
to give time for run plus shower plus food before work. This is good plan, \
question?

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
