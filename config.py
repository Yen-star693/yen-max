import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GROQ_KEY = os.getenv("GROQ_KEY")
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
