"""The recommended prompt block exists twice: in docs/ and in the panel."""

from __future__ import annotations

from pathlib import Path

PROMPT_BLOCK_DOC = Path("docs/prompt-block.md")
BLOCK_JS = Path("custom_components/elevenagents/panel/block.js")


def _doc_block() -> str:
    """Return the fenced markdown block in docs/prompt-block.md."""
    text = PROMPT_BLOCK_DOC.read_text()
    return text.split("```markdown\n", 1)[1].split("\n```", 1)[0]


def _panel_block() -> str:
    """Return RECOMMENDED_BLOCK from block.js, with its escaped backticks restored."""
    source = BLOCK_JS.read_text()
    literal = source.split("RECOMMENDED_BLOCK = `", 1)[1].rsplit("`;", 1)[0]
    return literal.replace("\\`", "`")


def test_the_panel_offers_the_block_the_docs_document() -> None:
    """A user who copies from either place gets the same text."""
    assert _panel_block() == _doc_block()


def test_the_block_opens_with_how_to_choose_the_entity() -> None:
    """The choosing rule comes before the tools are described."""
    block = _doc_block()

    assert block.index("## Choosing the entity") < block.index("## Using the tools")
    assert "Changing something the user did not ask for is worse than" in block


def test_the_block_tells_the_agent_how_to_confirm() -> None:
    """The new outcome is described where the agent reads the others."""
    block = _doc_block()

    assert "`confirmation_required`" in block
    assert "`confirmation_code`" in block


def test_the_block_reads_a_room_before_it_chooses() -> None:
    """A room and a kind of device can name more than one entity."""
    block = _doc_block()

    assert (
        "- The user names a room and a kind of device, not one entry: first call "
        "`ha_get_state` on\n  every entry of that kind in that room, then apply the "
        "two rules below to what it returns."
    ) in block
