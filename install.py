"""todo-keeper 설치 (여러 번 돌려도 같은 결과).

1) ~/.claude/skills/todo-keeper → 이 저장소 폴더로 정션(바로가기) 연결 — Claude가 스킬로 찾게
2) ~/.claude/settings.json SessionStart 훅에 hook-start 명령 등록 (이미 있으면 경로만 맞춤)

실행: python -I -X utf8 install.py          (확인만: --check)
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_LINK = Path.home() / ".claude" / "skills" / "todo-keeper"
SETTINGS = Path.home() / ".claude" / "settings.json"
PY = "C:/Users/playdata2/miniconda3/python.exe"
CMD = f'"{PY}" -I -X utf8 "{(ROOT / "scripts" / "todo.py").as_posix()}" hook-start'


def link_ok() -> bool:
    return SKILL_LINK.exists() and SKILL_LINK.resolve() == ROOT


def hook_entries(cfg: dict) -> list[dict]:
    return [h for grp in cfg.get("hooks", {}).get("SessionStart", []) for h in grp.get("hooks", [])
            if "todo.py" in h.get("command", "") and "hook-start" in h.get("command", "")]


def main() -> None:
    check = "--check" in sys.argv
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = hook_entries(cfg)
    print(f"스킬 연결: {'OK' if link_ok() else '없음/다른 곳'} ({SKILL_LINK} → {ROOT})")
    print(f"훅: {'OK' if hooks and all(h['command'] == CMD for h in hooks) else '없음/경로 다름'}")
    if check:
        return

    if not link_ok():
        if SKILL_LINK.exists():
            sys.exit(f"{SKILL_LINK} 에 다른 폴더가 있어요. 내용을 확인하고 옮긴 뒤 다시 실행하세요.")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(SKILL_LINK), str(ROOT)], check=True)
        print("스킬 연결 만듦")

    if hooks:
        for h in hooks:
            h["command"] = CMD
    else:
        cfg.setdefault("hooks", {}).setdefault("SessionStart", []).append(
            {"hooks": [{"type": "command", "command": CMD, "timeout": 20, "statusMessage": "할일 지킴이 점검"}]})
    SETTINGS.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("훅 등록/경로 맞춤 완료")


if __name__ == "__main__":
    main()
