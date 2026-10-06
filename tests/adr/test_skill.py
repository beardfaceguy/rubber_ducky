from pathlib import Path


SKILL = (
    Path(__file__).parents[2] / "skill" / "rubber_ducky_adr" / "SKILL.md"
)


def test_adr_skill_documents_external_reviewer_and_five_tools() -> None:
    skill = SKILL.read_text(encoding="utf-8")

    assert "../references/review-protocol.md" in skill
    assert "separate read-only reviewer" in skill
    for tool in ("start", "status", "respond", "rebut", "resume"):
        assert f"`rubber_ducky_adr_{tool}`" in skill
    assert "no automatic ADR reviewer-generation" in skill
    assert "human acceptance" in skill.lower()
