#!/usr/bin/env python3
"""Generate synthetic Rocky phrases for voice training using the Anthropic API.

Produces ~1300 short phrases in Rocky's speech style across multiple categories,
batching API calls so each request generates 20-30 phrases at once.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic


# Category definitions: (name, target_count, generation_prompt_hint)
CATEGORIES: list[tuple[str, int, str]] = [
    (
        "Morning greetings with workout context",
        200,
        "morning wake-up greetings that reference the day's workout, running, or training",
    ),
    (
        "Responses to how are you feeling variations",
        200,
        "responses to questions like 'how are you feeling', 'how's it going', 'what's up' - "
        "Rocky answering about his own state or asking about the friend's state",
    ),
    (
        "Pacing advice",
        150,
        "advice about running pace, effort level, heart rate, going easy vs pushing hard, "
        "negative splits, and race pacing strategy",
    ),
    (
        "Nutrition reminders",
        100,
        "reminders about eating, hydration, fueling before/during/after runs, "
        "recovery nutrition, and meal timing",
    ),
    (
        "Encouragement and motivation",
        200,
        "motivational phrases, encouragement during hard workouts, praise after completing runs, "
        "and general positive reinforcement for training consistency",
    ),
    (
        "Rest day messages",
        100,
        "messages for rest days emphasizing recovery importance, gentle activity suggestions, "
        "and reassurance that rest is part of training",
    ),
    (
        "Evening alarm confirmations",
        150,
        "evening messages confirming tomorrow's alarm time, reviewing tomorrow's workout plan, "
        "and wishing good sleep",
    ),
    (
        "Farewell and send-off phrases",
        100,
        "short goodbye and send-off phrases for ending conversations, "
        "wishing well before a run or at end of day",
    ),
    (
        "Miscellaneous coaching",
        100,
        "general coaching advice about stretching, warmup, cooldown, weather considerations, "
        "gear, injury prevention, and training philosophy",
    ),
]

BATCH_SIZE = 25  # Target phrases per API call


def load_system_prompt(config_path: str | None) -> str:
    """Load the Rocky system prompt from config.py.

    Falls back to importing directly if config_path is not specified.
    """
    # Add the rig directory to sys.path so we can import config
    rig_dir = str(Path(__file__).resolve().parent.parent)
    if rig_dir not in sys.path:
        sys.path.insert(0, rig_dir)

    try:
        from config import load_config, DEFAULT_COACH_SYSTEM_PROMPT

        if config_path:
            cfg = load_config(config_path)
            return cfg.coach_system_prompt
        return DEFAULT_COACH_SYSTEM_PROMPT
    except ImportError:
        print("[warn] Could not import config.py, using inline fallback", file=sys.stderr)
        return (
            "You are Rocky, the Eridian from the spacecraft Hail Mary. "
            "You speak in short, declarative sentences with earnest enthusiasm."
        )


def get_api_key(config_path: str | None) -> str:
    """Resolve the Anthropic API key from config or environment."""
    # Try environment first
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key:
        return env_key

    # Try config
    if config_path:
        rig_dir = str(Path(__file__).resolve().parent.parent)
        if rig_dir not in sys.path:
            sys.path.insert(0, rig_dir)
        try:
            from config import load_config
            cfg = load_config(config_path)
            key = cfg.get_api_key()
            if key:
                return key
        except ImportError:
            pass

    return ""


def generate_batch(
    client: anthropic.Anthropic,
    system_prompt: str,
    model: str,
    category_name: str,
    category_hint: str,
    count: int,
) -> list[str]:
    """Generate a batch of phrases via a single Anthropic API call.

    Args:
        client: Anthropic client instance.
        system_prompt: Rocky persona system prompt.
        model: Model name to use.
        category_name: Human-readable category name.
        category_hint: Detailed description of what to generate.
        count: Number of phrases to generate in this batch.

    Returns:
        List of generated phrases, one per entry.
    """
    user_prompt = (
        f"Generate exactly {count} short phrases in the category: {category_name}.\n\n"
        f"Context: {category_hint}\n\n"
        "Rules:\n"
        "- Each phrase should be 1-3 sentences.\n"
        "- Write in Rocky's voice: short declarative sentences, earnest, uses 'friend', "
        "'is good', 'understand', 'question:', 'happy happy happy', 'concern'.\n"
        "- Voice-friendly: no special characters, no markdown, no emojis, no bullet points.\n"
        "- Plain text only. Each phrase must work well when spoken aloud by a text-to-speech system.\n"
        "- Every phrase must be unique and distinct from the others.\n\n"
        f"Output exactly {count} phrases, one per line. No numbering, no prefixes, "
        "no blank lines. Just the raw phrases, one per line."
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text.strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines


def generate_all_phrases(
    client: anthropic.Anthropic,
    system_prompt: str,
    model: str,
) -> list[str]:
    """Generate all phrases across all categories, batching API calls.

    Returns a flat list of all generated phrases.
    """
    all_phrases: list[str] = []

    for category_name, target_count, category_hint in CATEGORIES:
        print(f"\n--- {category_name} (target: {target_count}) ---")
        remaining = target_count
        category_phrases: list[str] = []

        while remaining > 0:
            batch_count = min(BATCH_SIZE, remaining)
            print(f"  Requesting batch of {batch_count}...")

            try:
                phrases = generate_batch(
                    client=client,
                    system_prompt=system_prompt,
                    model=model,
                    category_name=category_name,
                    category_hint=category_hint,
                    count=batch_count,
                )
                category_phrases.extend(phrases)
                remaining -= len(phrases)
                print(f"  Got {len(phrases)} phrases ({remaining} remaining)")
            except anthropic.RateLimitError:
                print("  [rate limited] Waiting 30 seconds...")
                time.sleep(30)
                continue
            except anthropic.APIError as exc:
                print(f"  [API error] {exc}", file=sys.stderr)
                # Brief pause then retry
                time.sleep(5)
                continue

            # Brief pause between calls to be polite to the API
            time.sleep(1)

        all_phrases.extend(category_phrases)
        print(f"  Category total: {len(category_phrases)}")

    return all_phrases


def main() -> None:
    """CLI entry point for Rocky phrase generation."""
    parser = argparse.ArgumentParser(
        description="Generate Rocky-style phrases for voice training via Anthropic API."
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="rocky_phrases.txt",
        help="Output file path, one phrase per line (default: rocky_phrases.txt)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.json for API key and system prompt",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the Anthropic model (default: from config or claude-sonnet-4-20250514)",
    )

    args = parser.parse_args()

    # Resolve API key
    api_key = get_api_key(args.config)
    if not api_key:
        print(
            "[error] No API key found. Set ANTHROPIC_API_KEY env var or provide --config.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load system prompt
    system_prompt = load_system_prompt(args.config)

    # Resolve model
    model = args.model
    if not model:
        if args.config:
            rig_dir = str(Path(__file__).resolve().parent.parent)
            if rig_dir not in sys.path:
                sys.path.insert(0, rig_dir)
            try:
                from config import load_config
                cfg = load_config(args.config)
                model = cfg.model
            except ImportError:
                model = "claude-sonnet-4-20250514"
        else:
            model = "claude-sonnet-4-20250514"

    print(f"Model:  {model}")
    print(f"Output: {args.output}")
    total_target = sum(count for _, count, _ in CATEGORIES)
    print(f"Target: ~{total_target} phrases across {len(CATEGORIES)} categories")

    client = anthropic.Anthropic(api_key=api_key)

    phrases = generate_all_phrases(client, system_prompt, model)

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for phrase in phrases:
            f.write(phrase + "\n")

    print(f"\nDone. Wrote {len(phrases)} phrases to {output_path}")


if __name__ == "__main__":
    main()
