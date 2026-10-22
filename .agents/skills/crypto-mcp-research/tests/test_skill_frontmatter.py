from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

def test_skill_frontmatter_has_delimiters_name_and_description():
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", "SKILL.md must open with a --- line"
    assert "---" in lines[1:], "SKILL.md frontmatter has no closing --- line"
    block = lines[1:lines.index("---", 1)]
    assert not any(line.startswith("#") for line in block), "closing --- must precede the body"
    fields = dict(line.split(":", 1) for line in block if ":" in line)
    for key in ("name", "description"):
        assert fields.get(key, "").strip(), f"frontmatter is missing {key}"
