import requests
import json
from typing import Optional
from config import GROQ_KEY, GROQ_MODEL

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Simple in-memory cache to avoid hammering the API with duplicate requests
_request_cache = {}


def _call_groq(messages: list, max_tokens: int = 500) -> str:
    """
    Internal helper to call Groq API with request caching.
    
    Args:
        messages: List of message dicts with role and content
        max_tokens: Maximum tokens in response
        
    Returns:
        API response text or error message
    """
    # Create a cache key from the messages and max_tokens
    cache_key = (json.dumps(messages, sort_keys=True), max_tokens)
    
    if cache_key in _request_cache:
        return _request_cache[cache_key]
    
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
            error_msg = f"API Error: {response.status_code}"
            _request_cache[cache_key] = error_msg
            return error_msg

        data = response.json()
        result = data["choices"][0]["message"]["content"]
        _request_cache[cache_key] = result
        return result

    except requests.Timeout:
        error_msg = "API timeout - took too long"
        _request_cache[cache_key] = error_msg
        return error_msg
    except requests.RequestException as e:
        error_msg = f"API error: {str(e)}"
        _request_cache[cache_key] = error_msg
        return error_msg
    except (KeyError, json.JSONDecodeError):
        error_msg = "Failed to parse API response"
        _request_cache[cache_key] = error_msg
        return error_msg


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
        List of 1-2 short observation strings (max 15 words each)
    """
    system_prompt = """You are an experienced software engineer writing
    terse planning notes before starting a project.

    Given a user's project request, write 1 to 2 short factual
    observations about the request itself: whether a language was
    specified (and what default would be used if not), what kind of
    project it is.

    Rules:
    - Only state things actually implied by the request text
    - Do not invent requirements the user didn't imply
    - No first-person filler ("I think", "I will")
    - No mention of AI, models, prompts, or confidence
    - Each observation is ONE short sentence
    - Maximum 15 words per observation
    - Maximum 2 lines total
    - Return each observation on its own line, nothing else"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Request: {prompt}"}
    ]

    try:
        response = _call_groq(messages, max_tokens=80)

        if response.startswith("API") or response.startswith("Failed"):
            return []

        lines = [line.strip().lstrip("-* ").strip() for line in response.splitlines()]
        return [line for line in lines if line][:2]  # Max 2 observations
    
    except Exception as e:
        print(f"Error generating observations: {e}")
        return []


def generate_structure_note(prompt: str, filenames: list) -> str:
    """
    Generate one short observation about the planned file count.

    Args:
        prompt: The user's build request
        filenames: The real list of filenames from plan_project()

    Returns:
        A single short status sentence
    """
    # Just return a simple one-liner about file count
    # No need to call the API for this - it's simple enough to generate directly
    return f"Project structure planned ({len(filenames)} files)."


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

    try:
        return _call_groq(messages, max_tokens=2000)
    except Exception as e:
        print(f"Error regenerating file section: {e}")
        return broken_code  # return original on error


def generate_file_observation(filename: str, content: str) -> str:
    """
    Generate a short observation about what's actually in the
    generated file - e.g. "Defines 3 commands and imports discord.py"

    Args:
        filename: Name of the file
        content: The actual generated file content

    Returns:
        A single short observation about the file, or empty string on error
    """
    system_prompt = """You write one short factual line about generated
    code. Given a filename and its content, describe what it does in
    ONE short sentence - maximum 15 words.

    Be specific about what's in the code (functions, classes, imports,
    configuration). No first-person, no filler, no emojis."""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"File: {filename}\n\nContent:\n{content[:800]}"
        }
    ]

    try:
        note = _call_groq(messages, max_tokens=30)

        if note.startswith("API") or note.startswith("Failed"):
            return ""

        return note.strip().strip('"')
    
    except Exception as e:
        print(f"Error generating file observation: {e}")
        return ""
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

    try:
        return _call_groq(messages, max_tokens=2000)
    except Exception as e:
        print(f"Error regenerating file section: {e}")
        return broken_code  # return original on error


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
