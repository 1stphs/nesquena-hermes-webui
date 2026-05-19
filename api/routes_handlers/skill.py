"""Skill endpoint handlers re-exported by api.routes."""

import sys


def _routes_binding(name: str):
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, name):
        return getattr(routes, name)
    from api.helpers import bad, j, require

    return {
        "bad": bad,
        "j": j,
        "require": require,
    }[name]


def _handle_skill_save(handler, body):
    try:
        _routes_binding("require")(body, "name", "content")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    skill_name = body["name"].strip().lower().replace(" ", "-")
    if not skill_name or "/" in skill_name or ".." in skill_name:
        return _routes_binding("bad")(handler, "Invalid skill name")
    category = body.get("category", "").strip()
    if category and ("/" in category or ".." in category):
        return _routes_binding("bad")(handler, "Invalid category")
    from tools.skills_tool import SKILLS_DIR

    if category:
        skill_dir = SKILLS_DIR / category / skill_name
    else:
        skill_dir = SKILLS_DIR / skill_name
    try:
        skill_dir.resolve().relative_to(SKILLS_DIR.resolve())
    except ValueError:
        return _routes_binding("bad")(handler, "Invalid skill path")
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(body["content"], encoding="utf-8")
    return _routes_binding("j")(handler, {"ok": True, "name": skill_name, "path": str(skill_file)})
