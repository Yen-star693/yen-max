import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GROQ_KEY = os.getenv("GROQ_KEY")
GROQ_MODEL = "llama-3.1-8b-instant"
PREFIX = "yen "

if not TOKEN:
    raise ValueError("Missing TOKEN environment variable")

if not GROQ_KEY:
    raise ValueError("Missing GROQ_KEY environment variable")

# File configuration
ALLOWED_USERS_FILE = "allowed_users.json"
MAX_FILE_SIZE = 2097152  # 2MB Discord limit
MAX_MESSAGE_LENGTH = 2000  # Discord message limit

# Code detection keywords
CODE_KEYWORDS = [
    "code", "python", "discord", "bot", "script", "program",
    "function", "class", "command", "main.py", "html", "css",
    "javascript", "java", "c++", "c#", "cpp", "sql", "php",
    "lua", "go", "rust", "build", "create", "write", "make"
]

# Project planning keywords (determine if it's a multi-file project)
PROJECT_KEYWORDS = [
    "bot", "app", "project", "system", "framework", "application",
    "website", "discord", "tool", "utility"
]

# Explicit language/framework hints - if the prompt names one of these,
# it overrides the "no language specified -> Python" default
LANGUAGE_HINTS = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "js": "JavaScript",
    "ts": "TypeScript",
    "java": "Java",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "csharp": "C#",
    "php": "PHP",
    "go": "Go",
    "golang": "Go",
    "rust": "Rust",
    "lua": "Lua",
    "ruby": "Ruby",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
}

FRAMEWORK_HINTS = {
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "express": "Express",
    "react": "React",
    "vue": "Vue",
    "next.js": "Next.js",
    "nextjs": "Next.js",
    "discord.py": "discord.py",
    "discord.js": "discord.js",
    "spring": "Spring",
}

# ================= RESOURCE LIMITS =================
# These are checked BEFORE any API call is made, so a request that
# would exceed them never spends a single token.

MAX_FILES_PER_BUILD = 12          # Planner output is truncated to this
MAX_FILE_BYTES = 100_000          # ~100KB per generated file, upload-safe
MAX_TOTAL_BUILD_BYTES = 800_000   # Combined size across all files in a build
MAX_API_CALLS_PER_BUILD = 40      # Hard ceiling: planning + generation + validation + retries
MAX_TOKENS_PER_CALL = 2000        # Passed as the ceiling for any single _call_groq invocation

# ================= RETRY / REGENERATION LIMITS =================
MAX_REGENERATION_ATTEMPTS = 1     # How many times a single file may be auto-repaired
MAX_TRANSIENT_RETRIES = 2         # Retries for network drops / rate limits only

# ================= TIMEOUTS (seconds) =================
# Isolated per stage so one slow stage can't silently eat the whole build's budget
TIMEOUT_PLANNING = 15
TIMEOUT_GENERATION = 20
TIMEOUT_VALIDATION = 5             # Local AST parsing, should be near-instant
TIMEOUT_UPLOAD = 30

# ================= COOLDOWNS =================
BUILD_COOLDOWN_SECONDS = 30        # Per-user cooldown between `yen build` calls
MAX_CONCURRENT_BUILDS_PER_USER = 1 # A user can't start a second build while one is running

# ================= FILENAME SAFETY =================
# Filenames are rejected outright if they contain any of these patterns
FILENAME_BLOCKED_PATTERNS = ["..", "/", "\\", "~", "\0"]
MAX_FILENAME_LENGTH = 100
