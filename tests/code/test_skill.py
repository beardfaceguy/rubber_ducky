import re
from pathlib import Path

SKILL_ROOT = Path(__file__).parents[2] / "skill" / "agent-review"
REVIEW_LOG_ROOT = Path(__file__).parents[2] / "agent_review"


def test_skill_documents_cli_and_manual_fallback() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    for command in ("start", "status", "review", "respond", "rebut", "resume"):
        assert f"agent-review {command}" in skill
    for exit_code in ("`0`", "`2`", "`3`", "`4`", "`5`"):
        assert exit_code in skill
    for tool in ("start", "status", "generate", "respond", "rebut", "resume"):
        assert f"`agent_review_{tool}`" in skill
    assert "~/.config/agent_review/config.json" in skill
    assert "$XDG_CONFIG_HOME/agent_review/config.json" in skill
    assert "~/.config/agent_review/.env" in skill
    assert "project `.env` files are ignored" in skill
    assert "variable interpolation is off" in skill
    assert "If `agent-review` is unavailable" in skill
    assert "never write files or invoke CLI/MCP write" in skill


def test_skill_bundles_authoritative_protocol() -> None:
    protocol = (SKILL_ROOT / "references" / "review-protocol.md").read_text(
        encoding="utf-8"
    )

    assert "**Version:** 1.3" in protocol
    assert "Reviews are of the actual code/diff" in protocol


def test_vikunja_review_logs_use_absolute_database_ids() -> None:
    review_logs = tuple(REVIEW_LOG_ROOT.glob("*.md"))

    assert review_logs
    for review_log in review_logs:
        assert re.fullmatch(r"[0-9]{4,}-[a-z0-9-]+\.md", review_log.name)
        task_lines = [
            line
            for line in review_log.read_text(encoding="utf-8").splitlines()
            if line.startswith("**Task:**")
        ]
        assert len(task_lines) == 1
        assert re.fullmatch(
            r"\*\*Task:\*\* Vikunja [0-9]{4,} — .+",
            task_lines[0],
        )
