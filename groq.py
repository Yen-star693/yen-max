import requests
import json
from typing import Optional
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
            timeout=30
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


def generate_observations(prompt: str) -> list:
    """
    Generate real developer-style observations about the request itself -
    language choice, project type implications, likely file needs.

    These describe conclusions actually being used downstream (the
    language chosen here is the language passed to generate_file,
    the file-count estimate is compared against the real planner
    output), not decorative text.

    Args:
        prompt: The user's build request

    Returns:
        List of 1-3 short observation strings
    """
    system_prompt = """You are an experienced software engineer writing
    terse planning notes before starting a project, the kind you'd jot
    in a scratch file before writing code.

    Given a user's project request, write 1 to 3 short factual
    observations about the request itself: whether a language was
    specified (and what default would be used if not), what kind of
    project it is and what that implies (e.g. a Discord bot implies
    config/dependency files, a database need implies picking a
    default database, a website implies templates/static assets,
    auth implies extra config).

    Rules:
    - Only state things actually implied by the request text
    - Do not invent requirements the user didn't imply
    - No first-person filler ("I think", "I will")
    - No mention of AI, models, prompts, or confidence
    - Each observation is 1-2 short sentences
    - Return each observation on its own line, nothing else
    - Maximum 3 lines"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Request: {prompt}"}
    ]

    response = _call_groq(messages, max_tokens=150)

    if response.startswith("API") or response.startswith("Failed"):
        return []

    lines = [line.strip().lstrip("-* ").strip() for line in response.splitlines()]
    return [line for line in lines if line]


def generate_structure_note(prompt: str, filenames: list) -> str:
    """
    Generate one observation about the planned file structure,
    grounded in the actual filenames the planner returned.

    Args:
        prompt: The user's build request
        filenames: The real list of filenames from plan_project()

    Returns:
        A single short status sentence referencing the real file count
    """
    system_prompt = """You are an experienced software engineer. Given a
    project request and the actual list of files about to be generated,
    write ONE short sentence describing the structure - e.g. noting the
    file count, or why a particular file is needed (config, dependency
    list, templates folder, etc).

    Rules:
    - Reference only the given filenames, do not invent others
    - No first-person filler
    - No mention of AI, models, prompts, or confidence
    - One sentence, maximum 20 words"""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Request: {prompt}\nPlanned files: {', '.join(filenames)}"
        }
    ]

    note = _call_groq(messages, max_tokens=40)

    if note.startswith("API") or note.startswith("Failed"):
        return f"Project structure planned ({len(filenames)} files)."

    return note.strip().strip('"')


def regenerate_file_section(prompt: str, filename: str, broken_code: str, error_message: str, line_number: Optional[int]) -> str:
    """
    Ask the model to fix a real syntax error that validator.py actually
    found via ast.parse. The error message and line number passed in
    are real, not simulated.

    Args:
        prompt: Original project request
        filename: File being fixed
        broken_code: The code that failed validation
        error_message: The real error message from the parser
        line_number: The real line number of the error, if known

    Returns:
        Regenerated file content
    """
    location = f" near line {line_number}" if line_number else ""

    system_prompt = f"""You are a code generator fixing a real syntax error.

    File: {filename}
    Syntax error{location}: {error_message}

    Fix ONLY the syntax error. Preserve the original structure and intent.
    Return ONLY the corrected code, no explanations."""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Project request: {prompt}\n\nBroken code:\n{broken_code}"
        }
    ]

    return _call_groq(messages, max_tokens=2000)


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

    return _call_groq(messages, max_tokens=3000)


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
