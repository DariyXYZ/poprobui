from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import aiosqlite
import os
import json

from pdf import generate_pdf
from scoring import score_test

app = FastAPI(title="Попробуй API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "poprobui.db"


# ── DB init ────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                test_id TEXT NOT NULL,
                answers TEXT NOT NULL,
                scores TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


@app.on_event("startup")
async def startup():
    await init_db()


# ── Models ─────────────────────────────────────────────────────────────────

class SubmitAnswers(BaseModel):
    tg_user_id: int
    test_id: str
    answers: dict


class ResultResponse(BaseModel):
    result_id: int
    scores: dict
    top_types: list
    recommendations: list


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/submit", response_model=ResultResponse)
async def submit_answers(payload: SubmitAnswers):
    scores = score_test(payload.test_id, payload.answers)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO results (tg_user_id, test_id, answers, scores) VALUES (?, ?, ?, ?)",
            (
                payload.tg_user_id,
                payload.test_id,
                json.dumps(payload.answers, ensure_ascii=False),
                json.dumps(scores, ensure_ascii=False),
            )
        )
        result_id = cursor.lastrowid
        await db.commit()

    top_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    recommendations = _get_recommendations([t for t, _ in top_types])

    return ResultResponse(
        result_id=result_id,
        scores=scores,
        top_types=[t for t, _ in top_types],
        recommendations=recommendations,
    )


@app.get("/results/{tg_user_id}")
async def get_results(tg_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE tg_user_id = ? ORDER BY created_at DESC",
            (tg_user_id,)
        )
        rows = await cursor.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="No results found")
    return [dict(r) for r in rows]


@app.get("/pdf/{result_id}")
async def download_pdf(result_id: int, tg_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE id = ? AND tg_user_id = ?",
            (result_id, tg_user_id)
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Result not found")

    scores = json.loads(row["scores"])
    path = await generate_pdf(result_id, tg_user_id, scores)
    return FileResponse(path, media_type="application/pdf", filename=f"poprobui_report_{result_id}.pdf")


# ── Helpers ────────────────────────────────────────────────────────────────

PROFESSIONS = {
    "Инноватор": ["Продакт-менеджер", "UX-дизайнер", "Разработчик", "Стартапер", "Арт-директор"],
    "Специалист": ["Программист", "Аналитик данных", "Юрист", "Врач", "Инженер"],
    "Аналитик":   ["Data Analyst", "Финансист", "Экономист", "Исследователь", "Стратег"],
    "Коммуникатор": ["PR-менеджер", "Маркетолог", "Журналист", "Педагог", "Психолог"],
    "Менеджер":   ["Руководитель проекта", "Операционный директор", "Бизнес-аналитик"],
    "Предприниматель": ["Основатель стартапа", "Франчайзи", "Продюсер"],
}


def _get_recommendations(top_types: list) -> list:
    result = []
    for t in top_types:
        result.extend(PROFESSIONS.get(t, []))
    return result[:10]
