"""
IsiZulu Corpus Management System — Backend
Run locally:  uvicorn main:app --reload
Deploy:       Push to GitHub, connect to Render.com (free web service)
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import os, re, io, csv, json

# ── Database (Supabase / any PostgreSQL) ──────────────────────────────────────
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    return conn


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="IsiZulu Corpus API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
os.makedirs(os.path.join(UPLOAD_DIR, "inc"),  exist_ok=True)
os.makedirs(os.path.join(UPLOAD_DIR, "eipc"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_DIR, "ioc"),  exist_ok=True)


# ── Pydantic models ───────────────────────────────────────────────────────────

class INCTextSave(BaseModel):
    document_id: int
    text: str
    saved_by: Optional[str] = "anonymous"

class INCDocUpdate(BaseModel):
    title: Optional[str] = None
    domain: Optional[str] = None
    year: Optional[int] = None
    region: Optional[str] = None
    status: Optional[str] = None

class PairUpdate(BaseModel):
    en_text: Optional[str] = None
    zu_text: Optional[str] = None
    status: Optional[str] = None   # pending | verified | flagged

class TranscriptSave(BaseModel):
    audio_file_id: int
    corrected_text: str
    is_approved: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "IsiZulu CMS"}


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/dashboard")
def dashboard():
    conn = get_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT COALESCE(SUM(token_count),0) FROM inc_documents")
        tokens = cur.fetchone()["coalesce"]

        cur.execute("SELECT COUNT(*) FROM eipc_pairs WHERE status='verified'")
        pairs = cur.fetchone()["count"]

        cur.execute("SELECT COALESCE(SUM(duration_seconds),0) FROM ioc_files")
        seconds = cur.fetchone()["coalesce"]

        cur.execute("SELECT COUNT(*) FROM inc_documents WHERE status='needs_review'")
        pending_inc = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) FROM eipc_pairs WHERE status='pending'")
        pending_eipc = cur.fetchone()["count"]

        # Recent activity — last 8 saves across all corpora
        cur.execute("""
            (SELECT 'INC' as corpus, title as label, saved_by as actor,
                    word_count as value, 'words' as unit, saved_at as ts
             FROM inc_texts JOIN inc_documents ON inc_documents.id = inc_texts.document_id
             ORDER BY saved_at DESC LIMIT 4)
            UNION ALL
            (SELECT 'IOC' as corpus, f.filename as label, t.saved_by as actor,
                    0 as value, 'transcript' as unit, t.saved_at as ts
             FROM ioc_transcripts t JOIN ioc_files f ON f.id = t.audio_file_id
             ORDER BY t.saved_at DESC LIMIT 4)
            ORDER BY ts DESC LIMIT 8
        """)
        activity = [dict(r) for r in cur.fetchall()]
        for a in activity:
            if a["ts"]:
                a["ts"] = a["ts"].isoformat()

        return {
            "total_tokens": int(tokens),
            "verified_pairs": int(pairs),
            "ioc_hours": round(int(seconds) / 3600, 1),
            "pending_review": int(pending_inc) + int(pending_eipc),
            "recent_activity": activity,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# INC — IsiZulu National Corpus
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/inc/documents")
def list_inc_documents(status: Optional[str] = None, search: Optional[str] = None):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM inc_documents WHERE 1=1"
        params = []
        if status:
            sql += " AND status = %s"; params.append(status)
        if search:
            sql += " AND title ILIKE %s"; params.append(f"%{search}%")
        sql += " ORDER BY created_at DESC"
        cur.execute(sql, params)
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for k in ["created_at","updated_at"]:
                if d.get(k): d[k] = d[k].isoformat()
            result.append(d)
        return result
    finally:
        conn.close()


@app.post("/api/inc/documents")
async def add_inc_document(
    title: str = Form(...),
    domain: str = Form(None),
    year: int = Form(None),
    region: str = Form(None),
    uploaded_by: str = Form("anonymous"),
    file: UploadFile = File(None),
):
    conn = get_db()
    try:
        filepath = None
        filename = None
        if file and file.filename:
            contents = await file.read()
            safe = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            filepath = os.path.join(UPLOAD_DIR, "inc", safe)
            with open(filepath, "wb") as f:
                f.write(contents)
            filename = file.filename

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO inc_documents (title, filename, filepath, domain, year, region, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (title, filename, filepath, domain, year, region, uploaded_by))
        conn.commit()
        doc = dict(cur.fetchone())
        for k in ["created_at","updated_at"]:
            if doc.get(k): doc[k] = doc[k].isoformat()
        return doc
    finally:
        conn.close()


@app.patch("/api/inc/documents/{doc_id}")
def update_inc_document(doc_id: int, data: INCDocUpdate):
    conn = get_db()
    try:
        cur = conn.cursor()
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if not fields:
            raise HTTPException(400, "Nothing to update")
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        set_clause += ", updated_at=NOW()"
        cur.execute(
            f"UPDATE inc_documents SET {set_clause} WHERE id=%s RETURNING *",
            list(fields.values()) + [doc_id]
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        doc = dict(row)
        for k in ["created_at","updated_at"]:
            if doc.get(k): doc[k] = doc[k].isoformat()
        return doc
    finally:
        conn.close()


@app.post("/api/inc/save-text")
def save_inc_text(data: INCTextSave):
    conn = get_db()
    try:
        words = len(data.text.split()) if data.text.strip() else 0
        tokens = int(words * 1.3)

        cur = conn.cursor()
        # Save version
        cur.execute("""
            INSERT INTO inc_texts (document_id, text, word_count, saved_by)
            VALUES (%s,%s,%s,%s)
        """, (data.document_id, data.text, words, data.saved_by))

        # Update document totals
        cur.execute("""
            UPDATE inc_documents
            SET word_count=%s, token_count=%s, status='in_progress', updated_at=NOW()
            WHERE id=%s
        """, (words, tokens, data.document_id))

        conn.commit()
        return {"message": "Saved", "word_count": words, "token_count": tokens}
    finally:
        conn.close()


@app.get("/api/inc/documents/{doc_id}/text")
def get_inc_latest_text(doc_id: int):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT text, word_count, saved_by, saved_at
            FROM inc_texts
            WHERE document_id=%s
            ORDER BY saved_at DESC LIMIT 1
        """, (doc_id,))
        row = cur.fetchone()
        if not row:
            return {"text": "", "word_count": 0}
        d = dict(row)
        if d.get("saved_at"): d["saved_at"] = d["saved_at"].isoformat()
        return d
    finally:
        conn.close()


@app.get("/api/inc/stats")
def inc_stats():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total_docs,
                COALESCE(SUM(word_count),0) as total_words,
                COALESCE(SUM(token_count),0) as total_tokens,
                COUNT(*) FILTER (WHERE status='completed') as completed,
                COUNT(*) FILTER (WHERE status='in_progress') as in_progress,
                COUNT(*) FILTER (WHERE status='needs_review') as needs_review
            FROM inc_documents
        """)
        return dict(cur.fetchone())
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# EIPC — Parallel Corpus
# ══════════════════════════════════════════════════════════════════════════════

def split_sentences(text: str) -> List[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if len(s.strip()) > 5]


@app.post("/api/eipc/upload-en")
async def upload_en(
    file: UploadFile = File(...),
    title: str = Form(None),
    domain: str = Form(None),
    year: int = Form(None),
    uploaded_by: str = Form("anonymous"),
):
    contents = await file.read()
    safe = f"en_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, "eipc", safe)
    with open(filepath, "wb") as f:
        f.write(contents)

    text = contents.decode("utf-8", errors="ignore")
    sentences = split_sentences(text)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO eipc_documents (title, domain, year, en_filename, en_filepath, en_sentences, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (title or file.filename, domain, year, file.filename, filepath, len(sentences), uploaded_by))
        conn.commit()
        doc_id = cur.fetchone()["id"]
        return {"document_id": doc_id, "sentences": len(sentences), "preview": sentences[:3]}
    finally:
        conn.close()


@app.post("/api/eipc/upload-zu/{doc_id}")
async def upload_zu(doc_id: int, file: UploadFile = File(...)):
    contents = await file.read()
    safe = f"zu_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, "eipc", safe)
    with open(filepath, "wb") as f:
        f.write(contents)

    text = contents.decode("utf-8", errors="ignore")
    sentences = split_sentences(text)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE eipc_documents
            SET zu_filename=%s, zu_filepath=%s, zu_sentences=%s
            WHERE id=%s
        """, (file.filename, filepath, len(sentences), doc_id))
        conn.commit()
        return {"document_id": doc_id, "sentences": len(sentences), "preview": sentences[:3]}
    finally:
        conn.close()


@app.post("/api/eipc/align/{doc_id}")
def auto_align(doc_id: int, aligned_by: str = "anonymous"):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM eipc_documents WHERE id=%s", (doc_id,))
        doc = cur.fetchone()
        if not doc:
            raise HTTPException(404, "Document not found")
        if not doc["en_filepath"] or not doc["zu_filepath"]:
            raise HTTPException(400, "Both EN and ZU files must be uploaded first")

        with open(doc["en_filepath"], "r", errors="ignore") as f:
            en_sents = split_sentences(f.read())
        with open(doc["zu_filepath"], "r", errors="ignore") as f:
            zu_sents = split_sentences(f.read())

        created = 0
        for en, zu in zip(en_sents, zu_sents):
            # Simple length-ratio confidence heuristic
            ratio = min(len(en), len(zu)) / max(len(en), len(zu)) if max(len(en), len(zu)) > 0 else 0
            confidence = round(0.70 + ratio * 0.25, 2)
            cur.execute("""
                INSERT INTO eipc_pairs (source_doc_id, en_text, zu_text, confidence, status, aligned_by)
                VALUES (%s,%s,%s,%s,'pending',%s)
            """, (doc_id, en, zu, confidence, aligned_by))
            created += 1

        conn.commit()
        return {"pairs_created": created, "document_id": doc_id}
    finally:
        conn.close()


@app.get("/api/eipc/pairs")
def list_pairs(
    doc_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM eipc_pairs WHERE 1=1"
        params = []
        if doc_id:
            sql += " AND source_doc_id=%s"; params.append(doc_id)
        if status:
            sql += " AND status=%s"; params.append(status)
        if search:
            sql += " AND (en_text ILIKE %s OR zu_text ILIKE %s)"
            params += [f"%{search}%", f"%{search}%"]
        sql += " ORDER BY id OFFSET %s LIMIT %s"
        params += [skip, limit]
        cur.execute(sql, params)
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for k in ["created_at","verified_at"]:
                if d.get(k): d[k] = d[k].isoformat()
            result.append(d)

        # total count
        sql2 = "SELECT COUNT(*) FROM eipc_pairs WHERE 1=1"
        p2 = []
        if doc_id: sql2 += " AND source_doc_id=%s"; p2.append(doc_id)
        if status: sql2 += " AND status=%s"; p2.append(status)
        if search:
            sql2 += " AND (en_text ILIKE %s OR zu_text ILIKE %s)"
            p2 += [f"%{search}%", f"%{search}%"]
        cur.execute(sql2, p2)
        total = cur.fetchone()["count"]
        return {"total": int(total), "pairs": result}
    finally:
        conn.close()


@app.patch("/api/eipc/pairs/{pair_id}")
def update_pair(pair_id: int, data: PairUpdate):
    conn = get_db()
    try:
        cur = conn.cursor()
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if not fields:
            raise HTTPException(400, "Nothing to update")
        if "status" in fields and fields["status"] == "verified":
            fields["verified_at"] = datetime.utcnow()
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        cur.execute(
            f"UPDATE eipc_pairs SET {set_clause} WHERE id=%s RETURNING *",
            list(fields.values()) + [pair_id]
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Pair not found")
        d = dict(row)
        for k in ["created_at","verified_at"]:
            if d.get(k): d[k] = d[k].isoformat()
        return d
    finally:
        conn.close()


@app.get("/api/eipc/export/csv")
def export_csv(status: Optional[str] = "verified"):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT en_text, zu_text, confidence, status FROM eipc_pairs"
        params = []
        if status:
            sql += " WHERE status=%s"; params.append(status)
        sql += " ORDER BY id"
        cur.execute(sql, params)
        rows = cur.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["english", "isizulu", "confidence", "status"])
        for r in rows:
            writer.writerow([r["en_text"], r["zu_text"], r["confidence"], r["status"]])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=eipc_pairs.csv"}
        )
    finally:
        conn.close()


@app.get("/api/eipc/export/tmx")
def export_tmx(status: Optional[str] = "verified"):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT en_text, zu_text FROM eipc_pairs"
        params = []
        if status:
            sql += " WHERE status=%s"; params.append(status)
        cur.execute(sql, params)
        rows = cur.fetchall()

        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<tmx version="1.4"><header srclang="en"/><body>']
        for r in rows:
            en = r["en_text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            zu = r["zu_text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            lines.append(f'<tu><tuv xml:lang="en"><seg>{en}</seg></tuv>'
                         f'<tuv xml:lang="zu"><seg>{zu}</seg></tuv></tu>')
        lines.append("</body></tmx>")

        return StreamingResponse(
            io.BytesIO("\n".join(lines).encode()),
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=eipc_pairs.tmx"}
        )
    finally:
        conn.close()


@app.get("/api/eipc/stats")
def eipc_stats():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status='verified') as verified,
                COUNT(*) FILTER (WHERE status='pending') as pending,
                COUNT(*) FILTER (WHERE status='flagged') as flagged,
                ROUND(AVG(ARRAY_LENGTH(REGEXP_SPLIT_TO_ARRAY(TRIM(en_text),'\s+'),1)),1) as avg_en_words,
                ROUND(AVG(ARRAY_LENGTH(REGEXP_SPLIT_TO_ARRAY(TRIM(zu_text),'\s+'),1)),1) as avg_zu_words
            FROM eipc_pairs
        """)
        row = dict(cur.fetchone())
        # by domain
        cur.execute("""
            SELECT d.domain, COUNT(p.id) as pair_count
            FROM eipc_pairs p
            JOIN eipc_documents d ON d.id = p.source_doc_id
            GROUP BY d.domain ORDER BY pair_count DESC
        """)
        by_domain = [dict(r) for r in cur.fetchall()]
        row["by_domain"] = by_domain
        return row
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# IOC — Oral Corpus
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/ioc/files")
def list_ioc_files(status: Optional[str] = None):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM ioc_files WHERE 1=1"
        params = []
        if status:
            sql += " AND status=%s"; params.append(status)
        sql += " ORDER BY created_at DESC"
        cur.execute(sql, params)
        result = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result
    finally:
        conn.close()


@app.post("/api/ioc/upload")
async def upload_audio(
    file: UploadFile = File(...),
    region: str = Form(None),
    speaker_gender: str = Form(None),
    speaker_age_range: str = Form(None),
    topic: str = Form(None),
    duration_seconds: int = Form(None),
    uploaded_by: str = Form("anonymous"),
):
    contents = await file.read()
    safe = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, "ioc", safe)
    with open(filepath, "wb") as f:
        f.write(contents)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ioc_files
              (filename, filepath, region, speaker_gender, speaker_age_range, topic, duration_seconds, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (file.filename, filepath, region, speaker_gender, speaker_age_range,
              topic, duration_seconds, uploaded_by))
        conn.commit()
        d = dict(cur.fetchone())
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        return d
    finally:
        conn.close()


@app.post("/api/ioc/transcript")
def save_transcript(data: TranscriptSave):
    conn = get_db()
    try:
        cur = conn.cursor()
        # Upsert — update if exists, else insert
        cur.execute("SELECT id FROM ioc_transcripts WHERE audio_file_id=%s", (data.audio_file_id,))
        existing = cur.fetchone()
        if existing:
            cur.execute("""
                UPDATE ioc_transcripts
                SET corrected_text=%s, is_approved=%s, saved_at=NOW()
                WHERE audio_file_id=%s
            """, (data.corrected_text, data.is_approved, data.audio_file_id))
        else:
            cur.execute("""
                INSERT INTO ioc_transcripts (audio_file_id, corrected_text, is_approved)
                VALUES (%s,%s,%s)
            """, (data.audio_file_id, data.corrected_text, data.is_approved))

        new_status = "completed" if data.is_approved else "in_progress"
        cur.execute("UPDATE ioc_files SET status=%s WHERE id=%s",
                    (new_status, data.audio_file_id))
        conn.commit()
        return {"message": "Transcript saved", "status": new_status}
    finally:
        conn.close()


@app.get("/api/ioc/transcript/{file_id}")
def get_transcript(file_id: int):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ioc_transcripts WHERE audio_file_id=%s", (file_id,))
        row = cur.fetchone()
        if not row:
            return {"auto_text": None, "corrected_text": None, "is_approved": False}
        d = dict(row)
        if d.get("saved_at"): d["saved_at"] = d["saved_at"].isoformat()
        return d
    finally:
        conn.close()


@app.get("/api/ioc/stats")
def ioc_stats():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total_files,
                COALESCE(SUM(duration_seconds),0) as total_seconds,
                COUNT(*) FILTER (WHERE status='completed') as completed,
                COUNT(*) FILTER (WHERE status='in_progress') as in_progress
            FROM ioc_files
        """)
        row = dict(cur.fetchone())
        row["total_hours"] = round(int(row["total_seconds"]) / 3600, 1)
        return row
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════════════════════════════════════

CONTEXT = 6

def kwic_from_text(full_text: str, keyword: str, source: str, corpus: str, results: list, limit: int):
    if len(results) >= limit:
        return
    pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
    words = full_text.split()
    for i, word in enumerate(words):
        if pattern.match(word):
            left  = " ".join(words[max(0, i-CONTEXT):i])
            right = " ".join(words[i+1:i+1+CONTEXT])
            results.append({"left": left, "keyword": words[i], "right": right,
                             "source": source, "corpus": corpus})
            if len(results) >= limit:
                return


@app.get("/api/search/kwic")
def kwic_search(
    q: str = Query(..., min_length=2),
    corpus: str = Query(default="all"),
    limit: int = Query(default=50, le=200),
):
    conn = get_db()
    results = []
    try:
        cur = conn.cursor()

        if corpus in ("all","inc"):
            cur.execute("""
                SELECT t.text, d.title FROM inc_texts t
                JOIN inc_documents d ON d.id = t.document_id
                WHERE t.text ILIKE %s LIMIT 20
            """, (f"%{q}%",))
            for row in cur.fetchall():
                kwic_from_text(row["text"], q, f"INC · {row['title']}", "INC", results, limit)

        if corpus in ("all","eipc"):
            cur.execute("""
                SELECT zu_text, id FROM eipc_pairs
                WHERE zu_text ILIKE %s OR en_text ILIKE %s LIMIT 20
            """, (f"%{q}%", f"%{q}%"))
            for row in cur.fetchall():
                kwic_from_text(row["zu_text"], q, f"EIPC · pair {row['id']}", "EIPC", results, limit)

        if corpus in ("all","ioc"):
            cur.execute("""
                SELECT t.corrected_text, f.filename FROM ioc_transcripts t
                JOIN ioc_files f ON f.id = t.audio_file_id
                WHERE t.corrected_text ILIKE %s LIMIT 20
            """, (f"%{q}%",))
            for row in cur.fetchall():
                if row["corrected_text"]:
                    kwic_from_text(row["corrected_text"], q, f"IOC · {row['filename']}", "IOC", results, limit)

        return {"query": q, "total": len(results), "results": results[:limit]}
    finally:
        conn.close()


@app.get("/api/search/frequency")
def word_frequency(
    corpus: str = Query(default="inc"),
    top_n: int = Query(default=50, le=200),
):
    conn = get_db()
    try:
        cur = conn.cursor()
        all_text = ""

        if corpus == "inc":
            cur.execute("SELECT text FROM inc_texts")
            all_text = " ".join(r["text"] for r in cur.fetchall() if r["text"])
        elif corpus == "eipc_zu":
            cur.execute("SELECT zu_text FROM eipc_pairs WHERE status='verified'")
            all_text = " ".join(r["zu_text"] for r in cur.fetchall() if r["zu_text"])
        elif corpus == "eipc_en":
            cur.execute("SELECT en_text FROM eipc_pairs WHERE status='verified'")
            all_text = " ".join(r["en_text"] for r in cur.fetchall() if r["en_text"])
        elif corpus == "ioc":
            cur.execute("SELECT corrected_text FROM ioc_transcripts WHERE is_approved=true")
            all_text = " ".join(r["corrected_text"] for r in cur.fetchall() if r["corrected_text"])

        if not all_text.strip():
            return []

        words = re.findall(r'\b[a-zA-ZÀ-ÿ]{2,}\b', all_text.lower())
        total = len(words)
        freq: dict = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1

        top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {"rank": i+1, "word": w, "frequency": c,
             "per_million": int(c / total * 1_000_000) if total else 0}
            for i, (w, c) in enumerate(top)
        ]
    finally:
        conn.close()


@app.get("/api/search/stats")
def corpus_stats():
    conn = get_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT COALESCE(SUM(token_count),0) FROM inc_documents")
        tokens = cur.fetchone()["coalesce"]

        cur.execute("SELECT COUNT(*) FROM eipc_pairs WHERE status='verified'")
        pairs = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) FROM ioc_transcripts WHERE is_approved=true")
        transcripts = cur.fetchone()["count"]

        # tokens by domain
        cur.execute("""
            SELECT domain, COALESCE(SUM(token_count),0) as tokens
            FROM inc_documents GROUP BY domain ORDER BY tokens DESC
        """)
        by_domain = [dict(r) for r in cur.fetchall()]

        return {
            "total_tokens": int(tokens),
            "verified_pairs": int(pairs),
            "approved_transcripts": int(transcripts),
            "by_domain": by_domain,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SERVE FRONTEND
# ══════════════════════════════════════════════════════════════════════════════

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api"):
            raise HTTPException(404)
        index = os.path.join(static_dir, "index.html")
        return FileResponse(index) if os.path.exists(index) else HTTPException(404)
