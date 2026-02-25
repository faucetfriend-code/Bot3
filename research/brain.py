"""
brain.py — Jarvis Brain via Anthropic Messages API

Replaces Claude Code CLI invocation with direct API calls.
10x faster (no CC startup), no nested session conflicts,
supports vision natively.

Usage:
    brain = JarvisBrain()
    result = brain.interpret_signal(signal_data, image_paths)
    # result = {'action': 'EXECUTE', 'pair': 'BTCUSDT', ...}
"""

import anthropic
import base64
import json
import os
import logging
from pathlib import Path

logger = logging.getLogger('jarvis.brain')


class JarvisBrain:
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.getenv('ANTHROPIC_API_KEY')
        )
        self.model = os.getenv('BRAIN_MODEL', 'claude-sonnet-4-20250514')

    def interpret_signal(self, signal_data: dict, image_paths: list = None) -> dict:
        """
        Send a parsed signal (+ optional chart images) to Claude API.
        Returns structured trade decision as dict.
        """
        messages = self._build_messages(signal_data, image_paths)
        system_prompt = self._build_system_prompt()

        try:
            pair = signal_data.get('pair', '?')
            direction = signal_data.get('direction', '?')
            logger.info(f"🧠 Brain API call: {pair} {direction}")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system_prompt,
                messages=messages
            )

            result_text = response.content[0].text
            usage = response.usage
            logger.info(
                f"🧠 Brain responded: {len(result_text)} chars, "
                f"tokens: {usage.input_tokens}in/{usage.output_tokens}out"
            )

            return self._parse_response(result_text, signal_data)

        except Exception as e:
            logger.error(f"🧠 Brain API error: {e}")
            return {'action': 'SKIP', 'reason': f'API error: {e}'}

    def _build_system_prompt(self) -> str:
        return """You are Jarvis, a trading signal execution assistant.
You receive parsed trading signals from Discord analysts and decide whether to execute.

IMPORTANT RULES:
- Do NOT browse the web, fetch URLs, or install packages
- Respond ONLY with a JSON object (no markdown, no explanation outside the JSON)
- All data you need is provided in the message

RESPONSE FORMAT (strict JSON):
{
    "action": "EXECUTE" | "SKIP" | "CLOSE" | "UPDATE",
    "pair": "BTCUSDT",
    "direction": "LONG" | "SHORT",
    "entry": 97000.0,
    "sl": 96500.0,
    "tp": [98000.0, 99000.0],
    "leverage": 10,
    "confidence": 0.0-1.0,
    "reason": "brief explanation"
}

DECISION RULES:
- If the signal is a clear NEW_TRADE with entry + SL, return EXECUTE with the levels.
- If the signal is commentary/analysis (not actionable), return SKIP.
- If it's a close signal, return CLOSE with the pair.
- If it's an update (move SL, partial close), return UPDATE with details.
- If you can read chart levels from an image, extract entry/SL/TP from the drawn lines.
- If you CANNOT determine SL from the data or chart, return SKIP explaining why.
- Never return EXECUTE without a stop loss.
- For "CMP" or "market" entries, set entry to 0 (the system handles market orders).
- If the parser already extracted all levels and they look correct, confirm them.
- If levels look wrong (SL on wrong side, etc.), fix them or SKIP with explanation."""

    def _build_messages(self, signal_data: dict, image_paths: list = None) -> list:
        content = []

        # Add images first if present
        if image_paths:
            for img_path in image_paths:
                try:
                    with open(img_path, 'rb') as f:
                        img_data = base64.b64encode(f.read()).decode('utf-8')

                    suffix = Path(img_path).suffix.lower()
                    media_type = {
                        '.png': 'image/png',
                        '.jpg': 'image/jpeg',
                        '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif',
                        '.webp': 'image/webp'
                    }.get(suffix, 'image/png')

                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data
                        }
                    })
                    logger.info(f"🧠 Attached image: {img_path}")
                except Exception as e:
                    logger.warning(f"🧠 Failed to load image {img_path}: {e}")

        # Add signal text
        content.append({
            "type": "text",
            "text": json.dumps(signal_data, indent=2)
        })

        return [{"role": "user", "content": content}]

    def _parse_response(self, text: str, original_signal: dict) -> dict:
        """Extract JSON from response, handling markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith('```'):
            lines = cleaned.split('\n')
            lines = lines[1:]  # remove opening fence
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            cleaned = '\n'.join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"🧠 Failed to parse JSON response: {e}")
            logger.warning(f"🧠 Raw response: {text[:500]}")
            return {
                'action': 'SKIP',
                'reason': f'Failed to parse Brain response: {e}',
                'raw': text[:500]
            }
