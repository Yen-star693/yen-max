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


def plan_project(prompt: str) -> list:
    """
    Generate a list of filenames needed for a project.
    
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

    response = _call_groq(messages, max_tokens=200)

    try:
        # Extract JSON from response (in case there's extra text)
        start = response.find("[")
        end = response.rfind("]") + 1
        if start != -1 and end > start:
            json_str = response[start:end]
            filenames = json.loads(json_str)
            return filenames if isinstance(filenames, list) else []
    except (json.JSONDecodeError, ValueError):
        pass

    return []


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
