import requests
import json
from config import GROQ_KEY, GROQ_MODEL

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _call_groq(messages: list, max_tokens: int = 500) -> str:
    """
    Internal helper to call Groq API.
    
    Args:
        messages: List of message dicts with role and content
        max_tokens: Maximum tokens in response
        
    Returns:
        API response text or error message
    """
    try:
        response = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "max_tokens": max_tokens
            },
            timeout=10
        )

        if response.status_code != 200:
            return f"API Error: {response.status_code}"

        data = response.json()
        return data["choices"][0]["message"]["content"]

    except requests.Timeout:
        return "API timeout - took too long"
    except requests.RequestException as e:
        return f"API error: {str(e)}"
    except (KeyError, json.JSONDecodeError):
        return "Failed to parse API response"


def _parse_filenames(response: str) -> list:
    """
    Parse filenames out of a model response.

    Tries strict JSON first, then falls back to reading
    one filename per line (stripping bullets/numbers/quotes).

    Args:
        response: Raw model output

    Returns:
        List of filenames, or empty list if nothing usable was found
    """
    # Attempt 1: JSON array
    try:
        start = response.find("[")
        end = response.rfind("]") + 1
        if start != -1 and end > start:
            json_str = response[start:end]
            filenames = json.loads(json_str)
            if isinstance(filenames, list) and filenames:
                return [str(f).strip() for f in filenames if str(f).strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: newline-separated fallback
    filenames = []
    for line in response.splitlines():
        cleaned = line.strip()

        if not cleaned:
            continue

        # Strip common list prefixes: "-", "*", "1.", "1)", quotes, commas
        cleaned = cleaned.lstrip("-*0123456789.) ").strip()
        cleaned = cleaned.strip('",\'')

        # A filename should look like one (has an extension, no spaces)
        if cleaned and "." in cleaned and " " not in cleaned:
            filenames.append(cleaned)

    return filenames


def plan_project(prompt: str) -> list:
    """
    Generate a list of filenames needed for a project.

    Retries once on empty/failed parse, then falls back to
    newline parsing before giving up.

    Args:
        prompt: User's request for what to build
        
    Returns:
        List of filenames (e.g., ["main.py", "config.py", "README.md"])
    """
    system_prompt = """You are a project planning assistant. 
    
    Given a user request, return ONLY a JSON list of filenames needed.

    Example input: "make a discord bot"
    Example output: ["main.py", "commands.py", "config.py", "requirements.txt", "README.md"]

    Rules:
    - Return ONLY valid filenames
    - Include config/readme/requirements files
    - No explanations or extra text
    - Return valid JSON array only"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    # First attempt
    response = _call_groq(messages, max_tokens=200)
    filenames = _parse_filenames(response)

    if filenames:
        return filenames

    # Retry once (model responses can be flaky)
    response = _call_groq(messages, max_tokens=200)
    filenames = _parse_filenames(response)

    return filenames


def generate_dev_note(prompt: str, stage: str, context: str = "") -> str:
    """
    Generate a short developer-status-style note for the UI.

    This is NOT the model's real reasoning - it's a polished,
    human-readable status line describing what Yen Max is doing.

    Args:
        prompt: The user's original request
        stage: Which stage we're generating a note for
               ("understanding" or "planning")
        context: Extra context (e.g. planned filenames) for the note

    Returns:
        A single short status sentence
    """
    system_prompt = """You write short developer-status notes for a coding
    assistant's UI. These notes describe, in plain past/present tense,
    a practical observation about the current step - NOT internal reasoning,
    NOT chain of thought, just a clean one-sentence status update a developer
    might jot down.

    Rules:
    - One sentence only
    - No emojis
    - No first-person filler like "I think" or "I believe"
    - State it as a plain observation or decision
    - Maximum 20 words"""

    if stage == "understanding":
        user_content = f"User request: {prompt}\n\nWrite one status note about understanding what language/type of project this is."
    else:
        user_content = f"User request: {prompt}\n\nPlanned files: {context}\n\nWrite one status note about the project structure."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    note = _call_groq(messages, max_tokens=40)

    if note.startswith("API"):
        return ""

    return note.strip().strip('"')


def generate_file(prompt: str, filename: str) -> str:
    """
    Generate code for a specific file.
    
    Args:
        prompt: Original user request
        filename: Specific file to generate
        
    Returns:
        Generated file content
    """
    system_prompt = f"""You are a code generator. 
    
    Generate ONLY the code for the file: {filename}
    
    Rules:
    - No explanations
    - No comments about what you're doing
    - Just the raw code
    - Make it production-ready
    - Handle errors gracefully"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Project request: {prompt}\n\nGenerate: {filename}"}
    ]

    return _call_groq(messages, max_tokens=2000)


def ask_general(prompt: str) -> str:
    """
    Answer a general question (non-code).
    
    Args:
        prompt: User's question
        
    Returns:
        Response text
    """
    system_prompt = """You are a helpful Discord bot assistant.
    
    Keep responses:
    - Concise
    - Friendly
    - Discord-appropriate
    - Under 2000 characters when possible"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    return _call_groq(messages, max_tokens=500)
