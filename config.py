import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Easy to change later
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

PREFIX = "yen "