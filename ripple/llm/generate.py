import os

from dotenv import load_dotenv
from openai import OpenAI

from ripple.llm.prompts import SYSTEM_PROMPT, format_context
from ripple.retrieval.vector_store import RetrievedBlock


load_dotenv()


GENERATION_MODEL = "gpt-4o-mini"


def answer_question(
    question: str,
    blocks: list[RetrievedBlock],
    client: OpenAI | None = None,
) -> str:
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set"
            )

        client = OpenAI(api_key=api_key)

    user_message = (
        f"Question: {question}\n\n"
        f"Resource blocks:\n{format_context(blocks)}"
    )

    response = client.responses.create(
        model=GENERATION_MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_message,
    )

    return response.output_text
