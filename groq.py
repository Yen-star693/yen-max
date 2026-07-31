import requests

from config import GROQ_KEY, GROQ_MODEL
from prompts import SYSTEM_PROMPT


def ask_ai(prompt):

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_KEY}"
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": 700
        },
        timeout=30
    )

    r.raise_for_status()

    return r.json()["choices"][0]["message"]["content"]