from anthropic import Anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL
)

from prompts import SYSTEM_PROMPT

client = Anthropic(
    api_key=ANTHROPIC_API_KEY
)


def ask_claude(prompt):

    response = client.messages.create(
        model=CLAUDE_MODEL,
        system=SYSTEM_PROMPT,
        max_tokens=700,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response.content[0].text