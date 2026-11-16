"""todo.py 시나리오 시험 (11-13 설계 규칙). 실행: python -I -X utf8 tests/test_todo.py

실제 데이터(~/.claude/할일)는 건드리지 않고 임시 폴더(--data)와 가짜 날짜(--today)로 돈다.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TODO = Path(__file__).resolve().parents[1] / "scripts" / "todo.py"


class Todo:
    def __init__(self, d: Path):
        self.d = d

    def run(self, *args, today: str, git: bool = False, stdin: str = "") -> str:
        cmd = [sys.executable, "-I", "-X", "utf8", str(TODO), *args, "--data", str(self.d), "--today", today]
        if not git:
            cmd.append("--no-git")
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8")
        return (r.stdout + r.stderr).strip()

    def hook(self, today: str, cwd: str = "C:/x", git: bool = False) -> str:
        out = self.run("hook-start", today=today, git=git, stdin=json.dumps({"cwd": cwd}))
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def status(self, iid: int = 1) -> dict:
        db = json.loads((self.d / "projects.json").read_text(encoding="utf-8"))
        return next(it for it in db["items"] if it["id"] == iid)


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Todo(Path(self.tmp.name) / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_hook_prints_nothing(self):
        self.assertEqual(self.t.hook("2026-11-02"), "")

    def test_candidate_dedupe(self):
        self.t.run("candidate", "--name", "알파", "--next", "a", today="2026-11-02")
        out = self.t.run("candidate", "--name", "알파", "--next", "b", today="2026-11-02")
        self.assertIn("다음 한 걸음 갱신", out)
        self.assertEqual(self.t.status()["next"], "b")

    def test_candidate_shows_next_morning_only(self):
        self.t.run("candidate", "--name", "A", today="2026-11-13")
        self.assertNotIn("후보 #1", self.t.hook("2026-11-13"))
        self.assertIn("후보 #1 A", self.t.hook("2026-11-14"))

    def test_morning_asks_briefing_first(self):
        self.t.run("candidate", "--name", "A", today="2026-11-13")
        ctx = self.t.hook("2026-11-14")
        self.assertIn("브리핑부터", ctx)
        self.assertIn("sessions.py", ctx)
        self.assertIn("데일리 체크리스트", ctx)
        self.assertIn("history/", ctx)

    def test_weekly_asks_guide_edition(self):
        self.t.run("candidate", "--name", "A", today="2026-11-13")
        self.assertIn("정리 안내서", self.t.hook("2026-11-16"))

    def test_morning_skip(self):
        self.t.run("candidate", "--name", "A", today="2026-11-13")
        self.t.run("morning-done", "--skip", today="2026-11-14")
        self.assertNotIn("아침 점검을 아직", self.t.hook("2026-11-14"))
        self.assertIn("후보 #1 A", self.t.hook("2026-11-15"))

    def test_weekday_stall(self):
        self.t.run("candidate", "--name", "A", today="2026-11-02")
        self.t.run("choose", "1", "진행중", today="2026-11-03")  # 화
        self.t.run("list", today="2026-11-06")                    # 금: 수·목 2일
        self.assertEqual(self.t.status()["status"], "진행중")
        self.t.hook("2026-11-09")                                  # 월: 수·목·금 3일
        self.assertEqual(self.t.status()["status"], "멈춤")
        self.assertEqual(self.t.status()["nudges"], 1)

    def _stalled(self):
        self.t.run("candidate", "--name", "A", today="2026-11-02")
        self.t.run("choose", "1", "진행중", today="2026-11-03")
        self.t.hook("2026-11-09")

    def test_nudge_once_per_day(self):
        self._stalled()
        self.t.hook("2026-11-09")
        self.assertEqual(self.t.status()["nudges"], 1)

    def test_final_question_then_archive(self):
        self._stalled()
        self.t.hook("2026-11-10"); self.t.hook("2026-11-11")
        ctx = self.t.hook("2026-11-12")
        self.assertIn("보관할까요, 그만둘까요", ctx)
        self.t.hook("2026-11-13")
        self.assertEqual(self.t.status()["status"], "보관")

    def test_keep_going_on_final_day(self):
        self._stalled()
        for d in ("10", "11", "12"):
            self.t.hook(f"2026-11-{d}")
        self.t.run("activity", "1", today="2026-11-12")
        self.t.hook("2026-11-13")
        self.assertEqual(self.t.status()["status"], "진행중")
        self.assertEqual(self.t.status()["nudges"], 0)

    def test_candidate_auto_to_idea(self):
        self.t.run("candidate", "--name", "B", today="2026-11-02")
        self.t.run("list", today="2026-11-09")
        st = self.t.status()
        self.assertEqual((st["status"], st.get("auto_moved")), ("아이디어", True))

    def test_folder_session_counts_as_activity(self):
        folder = Path(self.tmp.name) / "proj"
        folder.mkdir()
        self.t.run("candidate", "--name", "A", "--folder", str(folder), today="2026-11-02")
        self.t.run("choose", "1", "진행중", today="2026-11-03")
        self.t.hook("2026-11-09", cwd=str(folder / "sub"))
        self.assertEqual(self.t.status()["status"], "진행중")

    def test_git_commit_counts_as_activity(self):
        folder = Path(self.tmp.name) / "repo"
        folder.mkdir()
        g = ["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(folder)], check=True)
        env = {"GIT_COMMITTER_DATE": "2026-11-06T10:00:00", "GIT_AUTHOR_DATE": "2026-11-06T10:00:00"}
        import os
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "x"], check=True, env={**os.environ, **env})
        self.t.run("candidate", "--name", "A", "--folder", str(folder), today="2026-11-02")
        self.t.run("choose", "1", "진행중", today="2026-11-03")
        self.t.run("list", today="2026-11-10", git=True)  # 금 커밋 → 월·화 쉼 = 1일
        st = self.t.status()
        self.assertEqual((st["status"], st["last_activity"]), ("진행중", "2026-11-06"))

    def test_complete_only_manual(self):
        self.t.run("candidate", "--name", "A", today="2026-11-02")
        self.t.run("choose", "1", "진행중", today="2026-11-03")
        self.t.run("complete", "1", today="2026-11-04")
        self.assertEqual(self.t.status()["status"], "완료")

    def test_archive_drop_ask_once(self):
        self.t.run("candidate", "--name", "A", today="2026-11-02")
        self.t.run("archive", "1", today="2026-11-25")
        self.assertIn("버릴까요", self.t.run("weekly", today="2026-12-28"))
        self.t.run("weekly-done", today="2026-12-28")
        self.assertNotIn("버릴까요", self.t.run("weekly", today="2027-01-04"))

    def test_focus_in_brief(self):
        self.t.run("candidate", "--name", "A", today="2026-11-02")
        self.t.run("choose", "1", "진행중", today="2026-11-03")
        self.t.run("focus", "1", today="2026-11-03")
        self.assertIn("⭐ 이번 주 집중: A", self.t.run("brief", today="2026-11-04"))

    def test_bad_input(self):
        self.t.run("candidate", "--name", "A", today="2026-11-02")
        self.assertIn("사용법", self.t.run("choose", "1", "완료", today="2026-11-02"))
        self.assertIn("없어요", self.t.run("complete", "99", today="2026-11-02"))
        self.assertEqual(self.t.status()["status"], "후보")


if __name__ == "__main__":
    unittest.main(verbosity=2)
