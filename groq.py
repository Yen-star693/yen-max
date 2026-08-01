import requests
import json
import time
from typing import Optional
from config import GROQ_KEY, GROQ_MODEL, MAX_TRANSIENT_RETRIES
from errors import TransientError, classify_http_error

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Cache only ever stores successful responses. Timeouts, HTTP errors,
# malformed payloads, and empty content are never written here, so a
# transient failure can never "poison" a future identical request.
_request_cache = {}


def _call_groq(messages: list, max_tokens: int = 500, _attempt: int = 0) -> str:
    """
    Call the Groq API. Retries automatically on transient failures
    (timeouts, rate limits, 5xx server errors) up to MAX_TRANSIENT_RETRIES.
    Permanent failures (auth, bad request, parse errors) fail immediately.

    Successful responses are cached; failures of any kind are not.

    Args:
        messages: List of message dicts with role and content
        max_tokens: Maximum tokens in response
        _attempt: Internal retry counter, do not set manually

    Returns:
        API response text. On exhausted retries or a permanent error,
        returns a short human-readable error string prefixed with
        "API" or "Failed" (callers already check for these prefixes).
    """
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
            error = classify_http_error(response.status_code)

            if isinstance(error, TransientError) and _attempt < MAX_TRANSIENT_RETRIES:
                time.sleep(0.5 * (_attempt + 1))  # small backoff
                return _call_groq(messages, max_tokens, _attempt=_attempt + 1)

            # Permanent, or retries exhausted - do not cache, just report
            return f"API Error: {response.status_code}"

        data = response.json()
        result = data["choices"][0]["message"]["content"]

        if not result or not result.strip():
            # Empty payload - do not cache, treat as a soft failure
            return "Failed: empty response"

        _request_cache[cache_key] = result
        return result

    except requests.Timeout:
        if _attempt < MAX_TRANSIENT_RETRIES:
            time.sleep(0.5 * (_attempt + 1))
            return _call_groq(messages, max_tokens, _attempt=_attempt + 1)
        return "API timeout - took too long"

    except requests.RequestException:
        if _attempt < MAX_TRANSIENT_RETRIES:
            time.sleep(0.5 * (_attempt + 1))
            return _call_groq(messages, max_tokens, _attempt=_attempt + 1)
        return "API error: connection failed"

    except (KeyError, json.JSONDecodeError):
        # Malformed response shape - permanent, do not retry or cache
        return "Failed to parse API response"


def clean_generated_code(raw: str) -> str:
    """
    Strip Markdown code fences and stray conversational text that the
    model sometimes adds despite instructions not to, before the result
    is ever handed to the validator. This prevents false syntax errors
    caused by a leading ```python fence or a trailing "Let me know if..."
    line rather than an actual code problem.

    Args:
        raw: Raw model output that is supposed to be pure code

    Returns:
        Cleaned code with fences and obvious chatter removed
    """
    if not raw:
        return raw

    text = raw.strip()

    # Strip a fenced code block if the whole response is wrapped in one,
    # e.g. ```python\n...\n``` or ```\n...\n```
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence (may have a language tag)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop trailing fence
        text = "\n".join(lines)

    # Some models wrap only part of the response in a fence; strip any
    # remaining stray fence markers rather than leaving them embedded
    text = text.replace("```python", "").replace("```javascript", "")
    text = text.replace("```", "")

    # Drop common conversational lead-ins/outros that occasionally slip
    # through even with "no explanations" in the system prompt
    conversational_prefixes = (
        "here's", "here is", "sure,", "sure!", "certainly", "of course",
        "i'll", "i will", "let me",
    )
    conversational_suffixes = (
        "let me know", "hope this helps", "feel free to", "would you like",
    )

    lines = text.splitlines()

    while lines and lines[0].strip().lower().startswith(conversational_prefixes):
        lines.pop(0)

    while lines and any(
        lines[-1].strip().lower().startswith(s) for s in conversational_suffixes
    ):
        lines.pop()

    return "\n".join(lines).strip()


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

    filenames = []
    for line in response.splitlines():
        cleaned = line.strip()

        if not cleaned:
            continue

        cleaned = cleaned.lstrip("-*0123456789.) ").strip()
        cleaned = cleaned.strip('",\'')

        if cleaned and "." in cleaned and " " not in cleaned:
            filenames.append(cleaned)

    return filenames


def detect_language_and_framework(prompt: str) -> tuple:
    """
    Check whether the user explicitly named a language or framework.

    This is a plain keyword check against config's hint tables, not a
    model call - it's used to decide whether to tell the planner/generator
    to honor an explicit choice instead of defaulting to Python.

    Args:
        prompt: The user's build request

    Returns:
        (language_or_None, framework_or_None)
    """
    from config import LANGUAGE_HINTS, FRAMEWORK_HINTS

    lower = prompt.lower()

    language = None
    for keyword, name in LANGUAGE_HINTS.items():
        if keyword in lower:
            language = name
            break

    framework = None
    for keyword, name in FRAMEWORK_HINTS.items():
        if keyword in lower:
            framework = name
            break

    return language, framework


def plan_project(prompt: str) -> list:
    """
    Generate a list of filenames needed for a project.

    Retries once on empty/failed parse, then falls back to
    newline parsing before giving up. If the user named an explicit
    language/framework, that's passed through so the planner doesn't
    default to Python file extensions for a JS request, etc.

    Args:
        prompt: User's request for what to build
        
    Returns:
        List of filenames (e.g., ["main.py", "config.py", "README.md"])
    """
    language, framework = detect_language_and_framework(prompt)

    hint = ""
    if language:
        hint += f" The user explicitly requested {language}."
    if framework:
        hint += f" The user explicitly requested the {framework} framework."

    system_prompt = f"""You are a project planning assistant. 
    
    Given a user request, return ONLY a JSON list of filenames needed.
    {hint}

    Example input: "make a discord bot"
    Example output: ["main.py", "commands.py", "config.py", "requirements.txt", "README.md"]

    Rules:
    - Return ONLY valid filenames
    - Include config/readme/requirements (or package.json etc) files as appropriate for the language
    - No explanations or extra text
    - Return valid JSON array only"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

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
    grounded in an actual keyword check (detect_language_and_framework),
    not a model guess, so what's shown always matches what's actually
    passed to the planner/generator downstream.

    Args:
        prompt: The user's build request

    Returns:
        List of 1-2 short observation strings
    """
    language, framework = detect_language_and_framework(prompt)

    observations = []
    if language:
        observations.append(f"Language explicitly requested: {language}.")
    else:
        observations.append("No language specified, Python will be used as default.")

    if framework:
        observations.append(f"Framework explicitly requested: {framework}.")

    return observations[:2]


def generate_structure_note(prompt: str, filenames: list) -> str:
    """
    Generate one short observation about the planned file count.

    Args:
        prompt: The user's build request
        filenames: The real list of filenames from plan_project()

    Returns:
        A single short status sentence
    """
    return f"Project structure planned ({len(filenames)} files)."


def regenerate_file_section(
    prompt: str,
    filename: str,
    broken_code: str,
    error_message: str,
    line_number: Optional[int]
) -> str:
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
        Regenerated file content, cleaned of any Markdown fences
    """
    location = f" near line {line_number}" if line_number else ""

    system_prompt = f"""You are a code generator fixing a real syntax error.

    File: {filename}
    Syntax error{location}: {error_message}

    Fix ONLY the syntax error. Preserve the original structure and intent.
    Return ONLY the corrected code, no explanations, no Markdown fences."""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Project request: {prompt}\n\nBroken code:\n{broken_code}"
        }
    ]

    result = _call_groq(messages, max_tokens=2000)

    if result.startswith("API") or result.startswith("Failed"):
        return broken_code  # return original on error

    return clean_generated_code(result)


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

    note = _call_groq(messages, max_tokens=30)

    if note.startswith("API") or note.startswith("Failed"):
        return ""

    return note.strip().strip('"')


def generate_file(prompt: str, filename: str) -> str:
    """
    Generate code for a specific file. Result is cleaned of Markdown
    fences and stray conversational text before being returned, so
    downstream validation only ever sees actual code content.
    
    Args:
        prompt: Original user request
        filename: Specific file to generate
        
    Returns:
        Generated file content
    """
    language, framework = detect_language_and_framework(prompt)

    hint = ""
    if language:
        hint += f" Write this in {language}."
    if framework:
        hint += f" Use the {framework} framework."

    system_prompt = f"""You are a code generator. 
    
    Generate ONLY the code for the file: {filename}
    {hint}
    
    Rules:
    - No explanations
    - No comments about what you're doing
    - No Markdown code fences
    - Just the raw code
    - Make it production-ready
    - Handle errors gracefully"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Project request: {prompt}\n\nGenerate: {filename}"}
    ]

    result = _call_groq(messages, max_tokens=3000)

    if result.startswith("API") or result.startswith("Failed"):
        return result  # pass the error through, caller already checks these prefixes

    return clean_generated_code(result)


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
