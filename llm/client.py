import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def get_client() -> OpenAI:
    """Crée un client OpenAI pointant vers le provider llm.lab."""
    api_key = os.getenv("LLM_LAB_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_LAB_API_KEY est absent du fichier .env")
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_LAB_BASE_URL", "https://llm.lab.sspcloud.fr/api/v1"),
    )


if __name__ == "__main__":
    client = get_client()
    response = client.chat.completions.create(
        model=os.environ["LLM_LAB_MODEL"],
        messages=[{"role": "user", "content": "Dis bonjour en une phrase."}],
    )
    print(response.choices[0].message.content)
