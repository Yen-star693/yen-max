import requests

from config import GROQ_KEY, GROQ_MODEL
from prompts import (
    PROJECT_PLANNER,
    FILE_GENERATOR,
    GENERAL_ASSISTANT
)

URL = "https://api.groq.com/openai/v1/chat/completions"

HEADERS = {
    "Authorization": f"Bearer {GROQ_KEY}"
}


def chat(system_prompt, user_prompt, max_tokens=700):

    r = requests.post(
        URL,
        headers=HEADERS,
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            "max_tokens": max_tokens
        },
        timeout=30
    )

    r.raise_for_status()

    return r.json()["choices"][0]["message"]["content"].strip()


def plan_project(prompt):

    response = chat(
        PROJECT_PLANNER,
        prompt,
        250
    )

    files = []

    for line in response.splitlines():

        line = line.strip()

        if line:
            files.append(line)

    return files


def generate_file(prompt, filename):

    return chat(
        FILE_GENERATOR,
        f"""
Project request:

{prompt}

Generate ONLY this file:

{filename}
""",
        1200
    )


def ask_general(prompt):

    return chat(
        GENERAL_ASSISTANT,
        prompt,
        700
    )