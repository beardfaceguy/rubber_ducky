from pathlib import Path

SKILL_ROOT = Path(__file__).parents[1] / "skill" / "agent-review"


def test_skill_documents_cli_and_manual_fallback() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    for command in ("start", "status", "respond", "rebut", "resume"):
        assert f"agent-review {command}" in skill
    for exit_code in ("`0`", "`2`", "`3`", "`4`", "`5`"):
        assert exit_code in skill
    for tool in ("start", "status", "respond", "rebut", "resume"):
        assert f"`agent_review_{tool}`" in skill
    assert "If `agent-review` is unavailable" in skill
    assert "never write files or invoke CLI/MCP write" in skill


def test_skill_bundles_authoritative_protocol() -> None:
    protocol = (SKILL_ROOT / "references" / "review-protocol.md").read_text(
        encoding="utf-8"
    )

    assert "**Version:** 1.3" in protocol
    assert "Reviews are of the actual code/diff" in protocol
