"""할일 지킴이 (todo-keeper) — 진행 중 프로젝트·후보·아이디어·보관함 상태 관리.

원본 데이터: ~/.claude/할일/projects.json  (사람이 보는 화면: 진행중.md 는 자동 생성)
실행: python -I -X utf8 todo.py <명령> ...

상태: 후보 → (아침 점검에서 고름) 진행중 / 아이디어 / 버림
      진행중 → 평일 3일 넘게 활동 없음 → 멈춤 → 권유 3번 → 마지막 질문 → 무응답이면 보관
      완료는 사용자가 직접 말할 때만 (complete 명령)

명령:
  hook-start                 SessionStart 훅 (stdin JSON) → Claude에게 보여 줄 문맥 출력
  candidate --name N [--next S] [--folder F] [--note T]   후보 등록 (같은 프로젝트가 진행중이면 다음 한 걸음만 갱신)
  choose ID 진행중|아이디어|버림   후보·아이디어·보관 항목을 옮김
  activity ID [--next S]     "오늘 했어" / "이어갈게" → 활동 기록 (멈춤이면 진행중 복귀)
  complete ID                사용자가 완료라고 말했을 때만
  archive ID | restore ID | drop ID
  edit ID [--name] [--next] [--folder] [--note]
  focus ID [ID...]           이번 주 집중 (주간 점검에서)
  morning-done [--skip]      아침 점검 끝 표시
  weekly-done                주간 점검 끝 표시
  list [--all]               목록
  weekly                     주간 점검 자료 (지난주 요약 + 정리할 목록)
  brief                      skill-scout 아침 디스코드 알림용 짧은 글
  render                     진행중.md 다시 만들기
공통 옵션: --today YYYY-MM-DD (시험용), --data DIR (시험용, 기본 ~/.claude/할일)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

STALL_WEEKDAYS = 3        # 평일 기준 이만큼 쉬면 멈춤
NUDGE_LIMIT = 3           # 멈춤 권유 횟수
CANDIDATE_DAYS = 7        # 후보를 이만큼 안 고르면 아이디어함으로
ARCHIVE_ASK_DAYS = 30     # 보관함에 이만큼 있으면 주간 점검에서 "버릴까요?" 한 번
ACTIVE = ("진행중", "멈춤")
SCRIPT = Path(__file__).resolve()
RUN = f'"C:/Users/playdata2/miniconda3/python.exe" -I -X utf8 "{SCRIPT.as_posix()}"'

# ---------------------------------------------------------------- 저장

def data_dir(arg: str | None) -> Path:
    return Path(arg) if arg else Path.home() / ".claude" / "할일"


def load(d: Path) -> dict:
    f = d / "projects.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"version": 1, "next_id": 1, "meta": {}, "items": []}


def save(d: Path, db: dict, today: date) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "projects.json.tmp"
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "projects.json")
    (d / "진행중.md").write_text(render_md(db, today), encoding="utf-8")


# ---------------------------------------------------------------- 날짜 도우미

def D(s: str) -> date:
    return date.fromisoformat(s)


def idle_weekdays(last: str, today: date) -> int:
    """last 다음 날부터 어제까지 중 평일 수 (오늘은 아직 끝나지 않았으므로 안 셈)."""
    n, d = 0, D(last) + timedelta(days=1)
    while d < today:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def week_monday(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def norm(p: str | None) -> str:
    return (p or "").replace("\\", "/").rstrip("/").lower()


def in_folder(cwd: str, folder: str) -> bool:
    c, f = norm(cwd), norm(folder)
    return bool(f) and (c == f or c.startswith(f + "/"))


def last_commit(folder: str) -> str | None:
    if not folder or not Path(folder).is_dir():
        return None
    try:
        r = subprocess.run(["git", "-C", folder, "log", "-1", "--format=%cs"],
                           capture_output=True, text=True, timeout=5)
        s = r.stdout.strip()
        return s if r.returncode == 0 and len(s) == 10 else None
    except Exception:
        return None


# ---------------------------------------------------------------- 상태 바꾸기

def log(it: dict, today: date, msg: str) -> None:
    it.setdefault("history", []).append([today.isoformat(), msg])


def set_status(it: dict, status: str, today: date, msg: str) -> None:
    it["status"] = status
    it["status_since"] = today.isoformat()
    log(it, today, msg)


def find(db: dict, iid: int) -> dict:
    for it in db["items"]:
        if it["id"] == iid:
            return it
    sys.exit(f"번호 {iid} 항목이 없어요.")


def mark_activity(it: dict, today: date, msg: str) -> None:
    t = today.isoformat()
    if (it.get("last_activity") or "") < t:
        it["last_activity"] = t
    if it["status"] == "멈춤":
        it["nudges"], it["final_asked"] = 0, ""
        set_status(it, "진행중", today, f"{msg} → 진행중 복귀")


def refresh(db: dict, today: date, commits: bool = True) -> None:
    """언제 돌려도 같은 결과가 나오는 정리 (활동 감지·멈춤·후보 만료·무응답 보관)."""
    t = today.isoformat()
    for it in db["items"]:
        st = it["status"]
        if st in ACTIVE:
            if commits:
                c = last_commit(it.get("folder", ""))
                if c and c <= t and c > (it.get("last_activity") or ""):
                    it["last_activity"] = c
                    log(it, today, f"커밋 감지 ({c})")
            idle = idle_weekdays(it["last_activity"], today)
            if st == "멈춤" and idle < STALL_WEEKDAYS:
                it["nudges"], it["final_asked"] = 0, ""
                set_status(it, "진행중", today, "활동 감지 → 진행중 복귀")
            elif st == "진행중" and idle >= STALL_WEEKDAYS:
                it["nudges"], it["final_asked"] = 0, ""
                set_status(it, "멈춤", today, f"평일 {idle}일 활동 없음 → 멈춤")
            elif st == "멈춤" and it.get("final_asked") and it["final_asked"] < t \
                    and it["last_activity"] <= it["final_asked"]:
                set_status(it, "보관", today, "마지막 질문에 응답 없음 → 보관")
        elif st == "후보" and (today - D(it["created"])).days >= CANDIDATE_DAYS:
            it["auto_moved"] = True
            set_status(it, "아이디어", today, f"{CANDIDATE_DAYS}일 동안 안 골라서 아이디어함으로 자동 이동")


def daily_nudge(db: dict, today: date) -> None:
    """그날 첫 대화에서 한 번만: 멈춤 항목 권유 횟수 올리기."""
    t = today.isoformat()
    meta = db.setdefault("meta", {})
    if meta.get("last_nudge_day") == t:
        return
    meta["last_nudge_day"] = t
    for it in db["items"]:
        if it["status"] != "멈춤":
            continue
        if it.get("nudges", 0) < NUDGE_LIMIT:
            it["nudges"] = it.get("nudges", 0) + 1
            it["last_nudge"] = t
            log(it, today, f"권유 {it['nudges']}/{NUDGE_LIMIT}")
        elif not it.get("final_asked"):
            it["final_asked"] = t
            log(it, today, "마지막 질문 (보관할까요, 그만둘까요?)")


# ---------------------------------------------------------------- 보여 주기

def by(db: dict, *status: str) -> list[dict]:
    return [it for it in db["items"] if it["status"] in status]


def line(it: dict, today: date) -> str:
    s = f"#{it['id']} {it['name']}"
    if it["status"] in ACTIVE:
        s += f" (평일 {idle_weekdays(it['last_activity'], today)}일째 쉼"
        if it["status"] == "멈춤":
            s += f", 권유 {it.get('nudges', 0)}/{NUDGE_LIMIT}"
            if it.get("final_asked"):
                s += ", 마지막 질문"
        s += ")"
    elif it["status"] == "후보":
        s += f" (등록 {it['created']}, {CANDIDATE_DAYS - (today - D(it['created'])).days}일 뒤 아이디어함)"
    elif it["status"] == "보관":
        days = (today - D(it["status_since"])).days
        s += f" (보관 {days}일째)"
    if it.get("next"):
        s += f" — 다음: {it['next']}"
    return s


def render_md(db: dict, today: date) -> str:
    out = [f"# 할일 지킴이 ({today.isoformat()} 기준)",
           "", "> 자동 생성 화면입니다. 고치려면 Claude에게 말하거나 todo.py 명령을 쓰세요. 원본은 projects.json.", ""]
    focus_week = week_monday(today)
    for st in ("멈춤", "진행중", "후보", "아이디어", "보관"):
        items = by(db, st)
        out.append(f"## {st} ({len(items)})")
        for it in items:
            star = " ⭐이번 주 집중" if it.get("focus") == focus_week else ""
            folder = f"  \n  폴더: `{it['folder']}`" if it.get("folder") else ""
            out.append(f"- {line(it, today)}{star}{folder}")
        out.append("")
    done = sorted(by(db, "완료"), key=lambda x: x.get("done_at", ""), reverse=True)[:20]
    out.append(f"## 최근 완료 ({len(by(db, '완료'))})")
    out += [f"- {it.get('done_at')} #{it['id']} {it['name']}" for it in done]
    return "\n".join(out) + "\n"


def weekly_text(db: dict, today: date) -> str:
    since = (today - timedelta(days=7)).isoformat()
    ev = {"완료": [], "진행중": [], "멈춤": [], "아이디어함 자동 이동": [], "보관": []}
    for it in db["items"]:
        for d, msg in it.get("history", []):
            if d < since:
                continue
            if msg.startswith("완료"):
                ev["완료"].append(it["name"])
            elif msg.startswith("아침 점검: 진행중") or msg.startswith("주간 점검: 진행중"):
                ev["진행중"].append(it["name"])
            elif "→ 멈춤" in msg:
                ev["멈춤"].append(it["name"])
            elif "아이디어함으로 자동 이동" in msg:
                ev["아이디어함 자동 이동"].append(it["name"])
            elif "→ 보관" in msg or msg.startswith("보관"):
                ev["보관"].append(it["name"])
    out = [f"## 주간 점검 ({week_monday(today)} 주)", "", "### 1. 지난주 요약"]
    for k, v in ev.items():
        v = sorted(set(v))
        out.append(f"- {k} {len(v)}개" + (f": {', '.join(v)}" if v else ""))
    out.append("\n### 2. 진행중·멈춤 (이어갈지 / 보관할지)")
    out += [f"- {line(it, today)}" for it in by(db, *ACTIVE)] or ["- 없음"]
    out.append("\n### 3. 보관함 (꺼낼지 / 계속 보관 / 버릴지)")
    for it in by(db, "보관"):
        ask = ""
        if (today - D(it["status_since"])).days >= ARCHIVE_ASK_DAYS and not it.get("drop_asked"):
            ask = f"  ← {ARCHIVE_ASK_DAYS}일 넘음: 이번에 한 번만 '버릴까요?' 묻기 (대답 없으면 그대로 둠)"
        out.append(f"- {line(it, today)}{ask}")
    if not by(db, "보관"):
        out.append("- 없음")
    out.append("\n### 4. 아이디어함 (진행중으로 올릴 것)")
    for it in by(db, "아이디어"):
        out.append(f"- {line(it, today)}" + ("  [후보에서 자동 이동]" if it.get("auto_moved") else ""))
    if not by(db, "아이디어"):
        out.append("- 없음")
    out.append("\n### 5. 이번 주 집중 1~2개 고르기 → focus 명령")
    return "\n".join(out)


def brief_text(db: dict, today: date) -> str:
    """skill-scout 디스코드 알림용. 비어 있으면 빈 문자열."""
    stalled, active, cands = by(db, "멈춤"), by(db, "진행중"), by(db, "후보")
    if not (stalled or active or cands or by(db, "아이디어", "보관")):
        return ""
    fw = week_monday(today)
    out = ["**📋 할일 지킴이**"]
    focus = [it for it in active + stalled if it.get("focus") == fw]
    if focus:
        out.append("⭐ 이번 주 집중: " + ", ".join(it["name"] for it in focus))
    if cands:
        out.append(f"🆕 고를 후보 {len(cands)}개: " + ", ".join(it["name"] for it in cands[:5]))
    for it in stalled:
        out.append(f"⏸️ 멈춤 — {line(it, today)}")
    if active:
        out.append("▶️ 진행중: " + ", ".join(f"{it['name']}" for it in active[:6]))
    if db.get("meta", {}).get("weekly_check_week") != fw:
        out.append(f"🗓️ 이번 주 주간 점검 아직: 보관함 {len(by(db, '보관'))}개 · 아이디어함 {len(by(db, '아이디어'))}개")
    if cands or stalled:
        out.append("→ 오늘 처음 여는 Claude 대화에서 고르면 돼요.")
    return "\n".join(out)


def hook_context(db: dict, today: date, cwd: str) -> str:
    t = today.isoformat()
    meta = db.get("meta", {})
    cands = [it for it in by(db, "후보") if it["created"] < t]  # 오늘 적은 후보는 다음 날 아침에
    stalled = by(db, "멈춤")
    here =[it for it in by(db, *ACTIVE) if in_folder(cwd, it.get("folder", ""))]
    morning = meta.get("last_morning_check") != t and (cands or stalled)
    weekly = meta.get("weekly_check_week") != week_monday(today) and db["items"]

    out = ["[할일 지킴이] (todo-keeper 스킬 규칙. 명령: " + RUN + " <명령>)"]
    if morning:
        out.append("■ 오늘 아침 점검을 아직 안 했습니다. 사용자의 첫 요청에 답할 때, 먼저 아래를 짧게 보여 주고 고르게 하세요 "
                   "(AskUserQuestion 가능). 사용자가 '나중에'라고 하면 morning-done --skip 후 바로 요청을 처리합니다.")
        for it in cands:
            out.append(f"  후보 {line(it, today)} → choose {it['id']} 진행중|아이디어|버림")
        for it in stalled:
            if it.get("final_asked") == t:
                out.append(f"  마지막 질문 {line(it, today)} → '보관할까요, 그만둘까요?' (archive/drop, 이어가면 activity). "
                           "대답 없으면 내일 자동 보관")
            elif it.get("last_nudge") == t:
                out.append(f"  권유 {line(it, today)} → 한 줄로 '이어갈까요?'만. 강요 금지. 이어가면 activity {it['id']}")
        out.append("  다 고르면 morning-done.")
    if weekly:
        out.append("■ 이번 주 주간 점검을 아직 안 했습니다. 아침 점검 뒤 'weekly' 명령 결과로 5단계(요약·진행중·보관함·아이디어함·집중)를 "
                   "5분 안에 진행하고 weekly-done. 사용자가 미루면 다음 대화에서 다시.")
    if here:
        out.append("■ 지금 폴더는 진행중 프로젝트입니다: " + "; ".join(line(it, today) for it in here))
    active = by(db, "진행중")
    if active and not here:
        out.append("■ 진행중: " + ", ".join(f"#{it['id']} {it['name']}" for it in active[:8]))
    out.append("■ 상시 규칙: ① 대화를 마칠 때 끝나지 않은 일이 있거나 사용자가 '나중에 ~하자'고 한 아이디어가 있으면, 묻지 말고 "
               "candidate로 한 줄 적고 마무리 메시지에 '후보에 적어 둠' 한 줄만 쓴다(같은 프로젝트가 진행중이면 --next만 갱신). "
               "② 진행 중인 작업에서 다른 주제로 넘어가면 '하던 ○○는 후보에 적어 둘게요' 한 줄만 쓰고 새 주제를 바로 따른다. "
               "③ 완료 처리는 사용자가 '○○ 완료'라고 말할 때만 하고, 확인 질문 한 번 뒤 complete. 완료를 먼저 권하지 않는다. "
               "④ 사용자가 '오늘 ○○ 했어'라고 하면 activity.")
    return "\n".join(out)


# ---------------------------------------------------------------- 명령

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--name"); ap.add_argument("--next"); ap.add_argument("--folder"); ap.add_argument("--note")
    ap.add_argument("--all", action="store_true"); ap.add_argument("--skip", action="store_true")
    ap.add_argument("--today"); ap.add_argument("--data"); ap.add_argument("--no-git", action="store_true")
    a = ap.parse_args()
    today = D(a.today) if a.today else date.today()
    d = data_dir(a.data)
    db = load(d)
    t = today.isoformat()
    commits = not a.no_git

    if a.cmd == "hook-start":
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except Exception:
            payload = {}
        cwd = payload.get("cwd") or os.getcwd()
        refresh(db, today, commits)
        for it in by(db, *ACTIVE):
            if in_folder(cwd, it.get("folder", "")) and it.get("last_activity") != t:
                mark_activity(it, today, "이 폴더에서 대화 열림")
        daily_nudge(db, today)
        save(d, db, today)
        if not db["items"]:
            return
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": hook_context(db, today, cwd)}}, ensure_ascii=False))
        return

    if a.cmd == "candidate":
        if not a.name:
            sys.exit("--name 이 필요해요.")
        for it in by(db, *ACTIVE, "후보"):
            same = it["name"].strip().lower() == a.name.strip().lower() or \
                (a.folder and norm(it.get("folder")) == norm(a.folder))
            if same:
                if a.next:
                    it["next"] = a.next
                if a.note:
                    it["note"] = a.note
                log(it, today, "후보 재등록 → 다음 한 걸음만 갱신")
                save(d, db, today)
                print(f"이미 {it['status']}: #{it['id']} {it['name']} (다음 한 걸음 갱신)")
                return
        it = {"id": db["next_id"], "name": a.name, "folder": a.folder or "", "next": a.next or "",
              "note": a.note or "", "status": "후보", "created": t, "status_since": t, "last_activity": t,
              "nudges": 0, "last_nudge": "", "final_asked": "", "history": []}
        log(it, today, "후보 등록")
        db["next_id"] += 1
        db["items"].append(it)
        save(d, db, today)
        print(f"후보 등록: #{it['id']} {it['name']}")
        return

    if a.cmd == "choose":
        if len(a.ids) != 2 or a.ids[1] not in ("진행중", "아이디어", "버림"):
            sys.exit("사용법: choose ID 진행중|아이디어|버림")
        it = find(db, int(a.ids[0]))
        where = "주간 점검" if it["status"] in ("아이디어", "보관") else "아침 점검"
        if a.ids[1] == "진행중":
            it["last_activity"], it["nudges"], it["final_asked"] = t, 0, ""
        set_status(it, a.ids[1], today, f"{where}: {a.ids[1]}")
        save(d, db, today)
        print(f"#{it['id']} {it['name']} → {a.ids[1]}")
        return

    if a.cmd in ("activity", "complete", "archive", "restore", "drop", "edit"):
        if len(a.ids) != 1:
            sys.exit(f"사용법: {a.cmd} ID")
        it = find(db, int(a.ids[0]))
        if a.cmd == "activity":
            if it["status"] not in ACTIVE:
                sys.exit(f"#{it['id']}는 {it['status']} 상태예요. 먼저 choose {it['id']} 진행중.")
            mark_activity(it, today, "사용자가 작업함")
            log(it, today, "활동 기록")
        elif a.cmd == "complete":
            it["done_at"] = t
            set_status(it, "완료", today, "완료 (사용자가 직접)")
        elif a.cmd == "archive":
            set_status(it, "보관", today, "보관 (사용자 선택)")
        elif a.cmd == "restore":
            it["last_activity"], it["nudges"], it["final_asked"], it["drop_asked"] = t, 0, "", False
            set_status(it, "진행중", today, "주간 점검: 진행중 (보관함에서 꺼냄)")
        elif a.cmd == "drop":
            set_status(it, "버림", today, "버림 (사용자 선택)")
        if a.name: it["name"] = a.name
        if a.next: it["next"] = a.next
        if a.folder is not None: it["folder"] = a.folder
        if a.note: it["note"] = a.note
        save(d, db, today)
        print(f"#{it['id']} {it['name']}: {it['status']}" + (f" · 다음: {it['next']}" if it.get("next") else ""))
        return

    if a.cmd == "focus":
        fw = week_monday(today)
        for it in db["items"]:
            if it.get("focus") == fw:
                it["focus"] = ""
        for i in a.ids:
            it = find(db, int(i))
            it["focus"] = fw
            log(it, today, "이번 주 집중")
        save(d, db, today)
        print("이번 주 집중: " + ", ".join(find(db, int(i))["name"] for i in a.ids))
        return

    if a.cmd == "morning-done":
        db.setdefault("meta", {})["last_morning_check"] = t
        save(d, db, today)
        print("아침 점검 " + ("미룸 (후보는 내일 다시)" if a.skip else "끝"))
        return

    if a.cmd == "weekly-done":
        db.setdefault("meta", {})["weekly_check_week"] = week_monday(today)
        for it in by(db, "보관"):
            if (today - D(it["status_since"])).days >= ARCHIVE_ASK_DAYS:
                it["drop_asked"] = True
        save(d, db, today)
        print("주간 점검 끝")
        return

    refresh(db, today, commits)
    save(d, db, today)
    if a.cmd == "list":
        shown = db["items"] if a.all else [it for it in db["items"] if it["status"] not in ("완료", "버림")]
        for it in shown:
            print(f"[{it['status']}] {line(it, today)}")
        if not shown:
            print("비어 있음")
    elif a.cmd == "weekly":
        print(weekly_text(db, today))
    elif a.cmd == "brief":
        print(brief_text(db, today))
    elif a.cmd == "render":
        print((d / "진행중.md").as_posix())
    else:
        sys.exit(f"모르는 명령: {a.cmd}")


if __name__ == "__main__":
    main()
