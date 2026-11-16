"""아침·주간 점검 브리핑용 — 최근 Claude 대화창마다 요청 흐름과 마지막 답을 뽑는다.

사용자가 후보 이름만 보고는 무슨 일인지 기억하지 못해서(2026-11-16), 고르기 전에
"어느 대화창에서 무엇을 정했고, 무엇이 새 요청에 묻혔는지"를 먼저 보여 주려고 만들었다.

실행: python -I -X utf8 sessions.py [--days 14] [--grep 단어] [--out 파일]
  --days  최근 며칠 안에 바뀐 대화만 (기본 14)
  --grep  제목·요청·마지막 답에 이 단어가 들어간 대화만 (후보 하나를 찾을 때)
  --out   화면 대신 파일로 저장 (Windows 콘솔 한글 깨짐 방지)
읽기만 하고 아무것도 고치지 않는다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

SKIP_PREFIX = ("<", "[Request interrupted", "Base directory", "Caveat", "This session is being continued")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def read_session(path: str) -> dict:
    title, users, last = None, [], ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            kind = o.get("type")
            if kind == "custom-title":
                title = o.get("customTitle") or title
            elif kind == "summary" and not title:
                title = o.get("summary")
            elif kind == "user" and not o.get("isMeta"):
                t = text_of((o.get("message") or {}).get("content")).strip()
                if t and not t.startswith(SKIP_PREFIX):
                    users.append(" ".join(t.split())[:110])
            elif kind == "assistant":
                t = text_of((o.get("message") or {}).get("content")).strip()
                if t:
                    last = " ".join(t.split())
    return {"title": title, "users": users, "last": last}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--grep", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--root", default=str(Path.home() / ".claude" / "projects"))
    a = ap.parse_args()

    since = (datetime.now() - timedelta(days=a.days)).timestamp()
    files = [p for p in glob.glob(os.path.join(a.root, "*", "*.jsonl")) if os.path.getmtime(p) >= since]
    files.sort(key=os.path.getmtime, reverse=True)

    out = []
    for p in files:
        s = read_session(p)
        if not s["users"]:
            continue
        blob = " ".join([s["title"] or ""] + s["users"] + [s["last"]])
        if a.grep and a.grep.lower() not in blob.lower():
            continue
        when = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d")
        out.append(f"### {when} {Path(p).stem[:8]} | {s['title']} | 요청 {len(s['users'])}개")
        out += [f" - {u}" for u in s["users"][-10:]]
        out.append(f" ▶ 마지막 답: {s['last'][-400:]}")
        out.append("")
    text = "\n".join(out) or "해당 대화 없음"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"{len(files)}개 중 저장: {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
