from types import SimpleNamespace

import pytest

from ripple.llm.generate import GENERATION_MODEL, answer_question
from ripple.llm.prompts import SYSTEM_PROMPT
from ripple.retrieval.vector_store import RetrievedBlock


class _FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, str]] = []

    def create(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input": input,
            }
        )
        return SimpleNamespace(output_text=self.output_text)


class _FakeOpenAIClient:
    def __init__(self, output_text: str) -> None:
        self.responses = _FakeResponses(output_text)


def test_answer_question_sends_grounded_request_and_returns_output() -> None:
    client = _FakeOpenAIClient("The VPC is created in main.tf:1-10.")
    block = RetrievedBlock(
        id=1,
        address="aws_vpc.main",
        file_path="main.tf",
        start_line=1,
        end_line=10,
        body='resource "aws_vpc" "main" {}',
        embed_text="aws_vpc.main",
        score=0.95,
    )

    answer = answer_question(
        "What creates the VPC?",
        [block],
        client=client,
    )

    assert answer == "The VPC is created in main.tf:1-10."
    assert client.responses.calls == [
        {
            "model": GENERATION_MODEL,
            "instructions": SYSTEM_PROMPT,
            "input": (
                "Question: What creates the VPC?\n\n"
                "Resource blocks:\n"
                "[1] aws_vpc.main\n"
                "    main.tf:1-10\n"
                '    resource "aws_vpc" "main" {}'
            ),
        }
    ]


def test_answer_question_requires_api_key_without_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(
        RuntimeError,
        match="OPENAI_API_KEY environment variable is not set",
    ):
        answer_question("What creates the VPC?", [])
