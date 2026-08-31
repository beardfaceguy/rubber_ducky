from pathlib import Path

SKILL_ROOT = Path(__file__).parents[2] / "skill" / "rubber_ducky_plan"


def test_plan_skill_documents_cli_mcp_and_plan_shape() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "name: rubber_ducky_plan" in skill
    for command in ("start", "status", "review", "respond", "rebut", "resume"):
        assert f"rubber-ducky-plan {command}" in skill
    for exit_code in ("`0`", "`2`", "`3`", "`4`", "`5`"):
        assert exit_code in skill
    for tool in ("start", "status", "generate", "respond", "rebut", "resume"):
        assert f"`rubber_ducky_plan_{tool}`" in skill
    for field in ("objective", "steps", "acceptance_criteria"):
        assert field in skill
    assert "~/.config/rubber_ducky/config.json" in skill
    assert "project `.env` files are ignored" in skill
    assert "If `rubber-ducky-plan` is unavailable" in skill
    assert "never write files or invoke CLI/MCP write" in skill


def test_plan_skill_defines_its_payload_and_links_shared_protocol() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "../references/review-protocol.md" in skill
    assert "Proposed Plan" in skill
    assert "Revised Plan" in skill
