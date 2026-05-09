import os
import warnings
import requests
from pathlib import Path
from dotenv import load_dotenv

# Suppress SSL warnings that appear when verify=False is used
warnings.filterwarnings("ignore", message="Unverified HTTPS request")
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# Load .env from project root explicitly
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found — check your .env file")

MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
API_URL = "https://api.groq.com/openai/v1/chat/completions"

HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
}


def call_llama(prompt: str) -> str:
    """Text-only LLM call."""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    response = requests.post(API_URL, headers=HEADERS, json=payload, verify=False)
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Groq API error: {response.status_code} – {response.text}") from e


def call_vision(prompt: str, image_b64: str) -> str:
    """Vision LLM call — sends image + text prompt."""
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
    }
    response = requests.post(API_URL, headers=HEADERS, json=payload, verify=False)
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Groq Vision API error: {response.status_code} – {response.text}") from e
