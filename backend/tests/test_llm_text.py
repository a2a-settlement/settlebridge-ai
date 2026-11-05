from types import SimpleNamespace

from app.services.llm_text import response_text


def test_response_text_skips_thinking_blocks():
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="..."),
            SimpleNamespace(type="text", text="answer"),
        ]
    )
    assert response_text(response) == "answer"


def test_response_text_without_text_is_empty():
    assert response_text(SimpleNamespace(content=[SimpleNamespace(type="thinking")])) == ""
    assert response_text(SimpleNamespace(content=[])) == ""
