import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GROQ_KEY = os.getenv("GROQ_KEY")

GROQ_MODEL = "llama-3.1-8b-instant"

PREFIX = "yen "