"""
IsiZulu Corpus Management System — Backend
Run locally:  uvicorn main:app --reload
Deploy:       Push to GitHub, connect to Render.com
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import os, re, io, csv, secrets

import psycopg2
from psycopg2.extras import RealDictCursor

def get_db():
    import urllib.parse
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    result = urllib.parse.urlparse(url)
    return psycopg2.connect(
        host=result.hostname, port=result.port or 5432,
        dbname=result.path.lstrip("/"), user=result.username,
        password=result.password, cursor_factory=RealDictCursor
    )

app = FastAPI(title="IsiZulu Corpus API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
os.makedirs(os.path.join(UPLOAD_DIR, "inc"),  exist_ok=True)
os.makedirs(os.path.join(UPLOAD_DIR, "eipc"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_DIR, "ioc"),  exist_ok=True)

# ── Auth ──────────────────────────────────────────────────────────────────────
APP_PASSWORD = os.environ.get("APP_PASSWORD", "ukzn2025!")
valid_tokens: set = set()
bearer = HTTPBearer(auto_error=False)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials or credentials.credentials not in valid_tokens:
        raise HTTPException(401, "Invalid or expired token — please log in again")
    return credentials.credentials

@app.post("/api/login")
def login(payload: dict):
    password = payload.get("password", "")
    if password != APP_PASSWORD:
        raise HTTPException(401, "Incorrect password")
    token = secrets.token_hex(32)
    valid_tokens.add(token)
    return {"token": token, "message": "Login successful"}

@app.post("/api/logout")
def logout(payload: dict):
    token = payload.get("token", "")
    valid_tokens.discard(token)
    return {"message": "Logged out"}

# ── Models ────────────────────────────────────────────────────────────────────
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
    status: Optional[str] = None

class TranscriptSave(BaseModel):
    audio_file_id: int
    corrected_text: str
    is_approved: bool = False

# ── Text helpers ──────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    text = text.replace('\x00', ' ')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[^\x09\x0A\x20-\x7E\x80-\xFF]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def split_sentences(text: str) -> List[str]:
    text = clean_text(text)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if len(s.strip()) > 10]

# ══════════════════════════════════════════════════════════════════════════════
# HEALTH (public)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "IsiZulu CMS"}

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD (protected)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/dashboard")
def dashboard(token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(token_count),0) as total FROM inc_documents")
        tokens = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) as total FROM eipc_pairs WHERE status='verified'")
        pairs = cur.fetchone()["total"]
        cur.execute("SELECT COALESCE(SUM(duration_seconds),0) as total FROM ioc_files")
        seconds = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) as total FROM inc_documents WHERE status='needs_review'")
        pending_inc = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) as total FROM eipc_pairs WHERE status='pending'")
        pending_eipc = cur.fetchone()["total"]
        cur.execute("""
            SELECT 'INC' as corpus, d.title as label, t.saved_by as actor,
                   t.word_count as value, t.saved_at as ts
            FROM inc_texts t JOIN inc_documents d ON d.id = t.document_id
            ORDER BY t.saved_at DESC LIMIT 8
        """)
        activity = [dict(r) for r in cur.fetchall()]
        for a in activity:
            if a["ts"]: a["ts"] = a["ts"].isoformat()
        return {
            "total_tokens": int(tokens), "verified_pairs": int(pairs),
            "ioc_hours": round(int(seconds) / 3600, 1),
            "pending_review": int(pending_inc) + int(pending_eipc),
            "recent_activity": activity,
        }
    except Exception as e:
        return {"error": str(e), "total_tokens": 0, "verified_pairs": 0,
                "ioc_hours": 0, "pending_review": 0, "recent_activity": []}
    finally:
        conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# INC (protected)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/inc/documents")
def list_inc_documents(status: Optional[str] = None, search: Optional[str] = None,
                       token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT id, title, domain, year, region, word_count, token_count, status, created_at, updated_at FROM inc_documents WHERE 1=1"
        params = []
        if status: sql += " AND status = %s"; params.append(status)
        if search: sql += " AND title ILIKE %s"; params.append(f"%{search}%")
        sql += " ORDER BY created_at DESC"
        cur.execute(sql, params)
        result = []
        for r in cur.fetchall():
            d = dict(r)
            for k in ["created_at","updated_at"]:
                if d.get(k): d[k] = d[k].isoformat()
            result.append(d)
        return result
    finally:
        conn.close()


@app.post("/api/inc/documents")
async def add_inc_document(
    title: str = Form(...), domain: str = Form(None),
    year: int = Form(None), region: str = Form(None),
    uploaded_by: str = Form("anonymous"), file: UploadFile = File(None),
    token: str = Depends(verify_token)
):
    conn = get_db()
    try:
        filepath = None; filename = None; ocr_text = ""
        if file and file.filename:
            contents = await file.read()
            safe = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            filepath = os.path.join(UPLOAD_DIR, "inc", safe)
            with open(filepath, "wb") as f: f.write(contents)
            filename = file.filename
            try:
                ocr_text = clean_text(contents.decode("utf-8", errors="ignore"))[:20000]
            except Exception:
                ocr_text = ""
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO inc_documents (title, filename, filepath, domain, year, region, uploaded_by, ocr_text)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, title, domain, year, region, word_count, token_count, status, created_at
        """, (title, filename, filepath, domain, year, region, uploaded_by, ocr_text))
        conn.commit()
        doc = dict(cur.fetchone())
        if doc.get("created_at"): doc["created_at"] = doc["created_at"].isoformat()
        return doc
    except Exception as e:
        conn.rollback(); raise HTTPException(500, f"Failed: {str(e)}")
    finally:
        conn.close()


@app.patch("/api/inc/documents/{doc_id}")
def update_inc_document(doc_id: int, data: INCDocUpdate, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if not fields: raise HTTPException(400, "Nothing to update")
        set_clause = ", ".join(f"{k}=%s" for k in fields) + ", updated_at=NOW()"
        cur.execute(f"UPDATE inc_documents SET {set_clause} WHERE id=%s RETURNING id, title, status",
                    list(fields.values()) + [doc_id])
        conn.commit()
        row = cur.fetchone()
        if not row: raise HTTPException(404, "Document not found")
        return dict(row)
    finally:
        conn.close()


@app.post("/api/inc/save-text")
def save_inc_text(data: INCTextSave, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        clean = clean_text(data.text)
        words = len(clean.split()) if clean.strip() else 0
        tokens = int(words * 1.3)
        cur = conn.cursor()
        cur.execute("INSERT INTO inc_texts (document_id, text, word_count, saved_by) VALUES (%s,%s,%s,%s)",
                    (data.document_id, clean, words, data.saved_by))
        cur.execute("UPDATE inc_documents SET word_count=%s, token_count=%s, status='in_progress', updated_at=NOW() WHERE id=%s",
                    (words, tokens, data.document_id))
        conn.commit()
        return {"message": "Saved", "word_count": words, "token_count": tokens}
    except Exception as e:
        conn.rollback(); raise HTTPException(500, f"Save failed: {str(e)}")
    finally:
        conn.close()


@app.get("/api/inc/documents/{doc_id}/text")
def get_inc_latest_text(doc_id: int, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, filepath, ocr_text FROM inc_documents WHERE id=%s", (doc_id,))
        doc = cur.fetchone()
        if not doc: raise HTTPException(404, "Document not found")
        ocr_text = ""
        if doc.get("ocr_text"):
            ocr_text = doc["ocr_text"]
        elif doc.get("filepath") and os.path.exists(doc["filepath"]):
            try:
                with open(doc["filepath"], "r", errors="ignore") as f:
                    ocr_text = clean_text(f.read())[:20000]
            except Exception:
                ocr_text = ""
        cur.execute("SELECT text, word_count, saved_by, saved_at FROM inc_texts WHERE document_id=%s ORDER BY saved_at DESC LIMIT 1", (doc_id,))
        row = cur.fetchone()
        if not row:
            return {"text": "", "word_count": 0, "ocr_text": ocr_text, "saved_at": None}
        d = dict(row)
        if d.get("saved_at"): d["saved_at"] = d["saved_at"].isoformat()
        d["ocr_text"] = ocr_text
        return d
    finally:
        conn.close()


@app.get("/api/inc/stats")
def inc_stats(token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as total_docs, COALESCE(SUM(word_count),0) as total_words,
                   COALESCE(SUM(token_count),0) as total_tokens,
                   COUNT(*) FILTER (WHERE status='completed') as completed,
                   COUNT(*) FILTER (WHERE status='in_progress') as in_progress,
                   COUNT(*) FILTER (WHERE status='needs_review') as needs_review
            FROM inc_documents
        """)
        row = dict(cur.fetchone())
        return {k: int(v) for k, v in row.items()}
    except Exception as e:
        return {"error": str(e), "total_docs": 0, "total_words": 0,
                "total_tokens": 0, "completed": 0, "in_progress": 0, "needs_review": 0}
    finally:
        conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# EIPC (protected)
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/api/eipc/upload-en")
async def upload_en(file: UploadFile = File(...), title: str = Form(None),
                    domain: str = Form(None), year: int = Form(None),
                    uploaded_by: str = Form("anonymous"), token: str = Depends(verify_token)):
    contents = await file.read()
    safe = f"en_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, "eipc", safe)
    with open(filepath, "wb") as f: f.write(contents)
    sentences = split_sentences(contents.decode("utf-8", errors="ignore"))
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
async def upload_zu(doc_id: int, file: UploadFile = File(...), token: str = Depends(verify_token)):
    contents = await file.read()
    safe = f"zu_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, "eipc", safe)
    with open(filepath, "wb") as f: f.write(contents)
    sentences = split_sentences(contents.decode("utf-8", errors="ignore"))
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE eipc_documents SET zu_filename=%s, zu_filepath=%s, zu_sentences=%s WHERE id=%s",
                    (file.filename, filepath, len(sentences), doc_id))
        conn.commit()
        return {"document_id": doc_id, "sentences": len(sentences), "preview": sentences[:3]}
    finally:
        conn.close()


@app.post("/api/eipc/align/{doc_id}")
def auto_align(doc_id: int, aligned_by: str = "anonymous", token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM eipc_documents WHERE id=%s", (doc_id,))
        doc = cur.fetchone()
        if not doc: raise HTTPException(404, "Document not found")
        if not doc["en_filepath"] or not doc["zu_filepath"]:
            raise HTTPException(400, "Both EN and ZU files must be uploaded first")
        if not os.path.exists(doc["en_filepath"]):
            raise HTTPException(400, "English file not found — please re-upload")
        if not os.path.exists(doc["zu_filepath"]):
            raise HTTPException(400, "IsiZulu file not found — please re-upload")
        with open(doc["en_filepath"], "r", errors="ignore") as f: en_sents = split_sentences(f.read())
        with open(doc["zu_filepath"], "r", errors="ignore") as f: zu_sents = split_sentences(f.read())
        if not en_sents: raise HTTPException(400, "No sentences in English file")
        if not zu_sents: raise HTTPException(400, "No sentences in IsiZulu file")
        created = 0
        for en, zu in zip(en_sents, zu_sents):
            ratio = min(len(en), len(zu)) / max(len(en), len(zu)) if max(len(en), len(zu)) > 0 else 0
            confidence = round(0.70 + ratio * 0.25, 2)
            cur.execute("""
                INSERT INTO eipc_pairs (source_doc_id, en_text, zu_text, confidence, status, aligned_by)
                VALUES (%s,%s,%s,%s,'pending',%s)
            """, (doc_id, en, zu, confidence, aligned_by))
            created += 1
        conn.commit()
        return {"pairs_created": created, "document_id": doc_id}
    except HTTPException: raise
    except Exception as e:
        conn.rollback(); raise HTTPException(500, f"Alignment failed: {str(e)}")
    finally:
        conn.close()


@app.get("/api/eipc/pairs")
def list_pairs(doc_id: Optional[int] = None, status: Optional[str] = None,
               search: Optional[str] = None, skip: int = 0, limit: int = 50,
               token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM eipc_pairs WHERE 1=1"; params = []
        if doc_id: sql += " AND source_doc_id=%s"; params.append(doc_id)
        if status: sql += " AND status=%s"; params.append(status)
        if search:
            sql += " AND (en_text ILIKE %s OR zu_text ILIKE %s)"; params += [f"%{search}%", f"%{search}%"]
        sql += " ORDER BY id OFFSET %s LIMIT %s"; params += [skip, limit]
        cur.execute(sql, params)
        result = []
        for r in cur.fetchall():
            d = dict(r)
            for k in ["created_at","verified_at"]:
                if d.get(k): d[k] = d[k].isoformat()
            result.append(d)
        sql2 = "SELECT COUNT(*) FROM eipc_pairs WHERE 1=1"; p2 = []
        if doc_id: sql2 += " AND source_doc_id=%s"; p2.append(doc_id)
        if status: sql2 += " AND status=%s"; p2.append(status)
        if search:
            sql2 += " AND (en_text ILIKE %s OR zu_text ILIKE %s)"; p2 += [f"%{search}%", f"%{search}%"]
        cur.execute(sql2, p2)
        return {"total": int(cur.fetchone()["count"]), "pairs": result}
    finally:
        conn.close()


@app.patch("/api/eipc/pairs/{pair_id}")
def update_pair(pair_id: int, data: PairUpdate, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        fields = {k: v for k, v in data.model_dump().items() if v is not None}
        if not fields: raise HTTPException(400, "Nothing to update")
        if "status" in fields and fields["status"] == "verified":
            fields["verified_at"] = datetime.utcnow()
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        cur.execute(f"UPDATE eipc_pairs SET {set_clause} WHERE id=%s RETURNING *",
                    list(fields.values()) + [pair_id])
        conn.commit()
        row = cur.fetchone()
        if not row: raise HTTPException(404, "Pair not found")
        d = dict(row)
        for k in ["created_at","verified_at"]:
            if d.get(k): d[k] = d[k].isoformat()
        return d
    finally:
        conn.close()


@app.get("/api/eipc/export/csv")
def export_csv(status: Optional[str] = "verified", token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT en_text, zu_text, confidence, status FROM eipc_pairs"
        params = []
        if status: sql += " WHERE status=%s"; params.append(status)
        cur.execute(sql + " ORDER BY id", params)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["english","isizulu","confidence","status"])
        for r in cur.fetchall(): writer.writerow([r["en_text"], r["zu_text"], r["confidence"], r["status"]])
        output.seek(0)
        return StreamingResponse(io.BytesIO(output.getvalue().encode()), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=eipc_pairs.csv"})
    finally:
        conn.close()


@app.get("/api/eipc/export/tmx")
def export_tmx(status: Optional[str] = "verified", token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT en_text, zu_text FROM eipc_pairs"
        params = []
        if status: sql += " WHERE status=%s"; params.append(status)
        cur.execute(sql, params)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tmx version="1.4"><header srclang="en"/><body>']
        for r in cur.fetchall():
            en = r["en_text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            zu = r["zu_text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            lines.append(f'<tu><tuv xml:lang="en"><seg>{en}</seg></tuv><tuv xml:lang="zu"><seg>{zu}</seg></tuv></tu>')
        lines.append("</body></tmx>")
        return StreamingResponse(io.BytesIO("\n".join(lines).encode()), media_type="application/xml",
                                 headers={"Content-Disposition": "attachment; filename=eipc_pairs.tmx"})
    finally:
        conn.close()


@app.get("/api/eipc/stats")
def eipc_stats(token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE status='verified') as verified,
                   COUNT(*) FILTER (WHERE status='pending') as pending,
                   COUNT(*) FILTER (WHERE status='flagged') as flagged,
                   ROUND(AVG(ARRAY_LENGTH(REGEXP_SPLIT_TO_ARRAY(TRIM(en_text),'\s+'),1)),1) as avg_en_words,
                   ROUND(AVG(ARRAY_LENGTH(REGEXP_SPLIT_TO_ARRAY(TRIM(zu_text),'\s+'),1)),1) as avg_zu_words
            FROM eipc_pairs
        """)
        row = dict(cur.fetchone())
        cur.execute("""
            SELECT d.domain, COUNT(p.id) as pair_count FROM eipc_pairs p
            JOIN eipc_documents d ON d.id = p.source_doc_id GROUP BY d.domain ORDER BY pair_count DESC
        """)
        row["by_domain"] = [dict(r) for r in cur.fetchall()]
        return row
    finally:
        conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# IOC (protected)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/ioc/files")
def list_ioc_files(status: Optional[str] = None, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM ioc_files WHERE 1=1"; params = []
        if status: sql += " AND status=%s"; params.append(status)
        cur.execute(sql + " ORDER BY created_at DESC", params)
        result = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result
    finally:
        conn.close()


@app.post("/api/ioc/upload")
async def upload_audio(file: UploadFile = File(...), region: str = Form(None),
                       speaker_gender: str = Form(None), speaker_age_range: str = Form(None),
                       topic: str = Form(None), duration_seconds: int = Form(None),
                       uploaded_by: str = Form("anonymous"), token: str = Depends(verify_token)):
    contents = await file.read()
    safe = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, "ioc", safe)
    with open(filepath, "wb") as f: f.write(contents)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ioc_files (filename, filepath, region, speaker_gender, speaker_age_range, topic, duration_seconds, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (file.filename, filepath, region, speaker_gender, speaker_age_range, topic, duration_seconds, uploaded_by))
        conn.commit()
        d = dict(cur.fetchone())
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        return d
    finally:
        conn.close()


@app.post("/api/ioc/transcript")
def save_transcript(data: TranscriptSave, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM ioc_transcripts WHERE audio_file_id=%s", (data.audio_file_id,))
        if cur.fetchone():
            cur.execute("UPDATE ioc_transcripts SET corrected_text=%s, is_approved=%s, saved_at=NOW() WHERE audio_file_id=%s",
                        (data.corrected_text, data.is_approved, data.audio_file_id))
        else:
            cur.execute("INSERT INTO ioc_transcripts (audio_file_id, corrected_text, is_approved) VALUES (%s,%s,%s)",
                        (data.audio_file_id, data.corrected_text, data.is_approved))
        new_status = "completed" if data.is_approved else "in_progress"
        cur.execute("UPDATE ioc_files SET status=%s WHERE id=%s", (new_status, data.audio_file_id))
        conn.commit()
        return {"message": "Transcript saved", "status": new_status}
    finally:
        conn.close()


@app.get("/api/ioc/transcript/{file_id}")
def get_transcript(file_id: int, token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ioc_transcripts WHERE audio_file_id=%s", (file_id,))
        row = cur.fetchone()
        if not row: return {"auto_text": None, "corrected_text": None, "is_approved": False}
        d = dict(row)
        if d.get("saved_at"): d["saved_at"] = d["saved_at"].isoformat()
        return d
    finally:
        conn.close()


@app.get("/api/ioc/stats")
def ioc_stats(token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as total_files, COALESCE(SUM(duration_seconds),0) as total_seconds,
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
# SEARCH (protected)
# ══════════════════════════════════════════════════════════════════════════════
CONTEXT = 6

def kwic_from_text(full_text, keyword, source, corpus, results, limit):
    if len(results) >= limit: return
    pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
    words = full_text.split()
    for i, word in enumerate(words):
        if pattern.match(word):
            results.append({"left": " ".join(words[max(0,i-CONTEXT):i]), "keyword": words[i],
                             "right": " ".join(words[i+1:i+1+CONTEXT]), "source": source, "corpus": corpus})
            if len(results) >= limit: return


@app.get("/api/search/kwic")
def kwic_search(q: str = Query(..., min_length=2), corpus: str = Query(default="all"),
                limit: int = Query(default=50, le=200), token: str = Depends(verify_token)):
    conn = get_db(); results = []
    try:
        cur = conn.cursor()
        if corpus in ("all","inc"):
            cur.execute("SELECT t.text, d.title FROM inc_texts t JOIN inc_documents d ON d.id=t.document_id WHERE t.text ILIKE %s LIMIT 20", (f"%{q}%",))
            for row in cur.fetchall(): kwic_from_text(row["text"], q, f"INC · {row['title']}", "INC", results, limit)
        if corpus in ("all","eipc"):
            cur.execute("SELECT zu_text, id FROM eipc_pairs WHERE zu_text ILIKE %s OR en_text ILIKE %s LIMIT 20", (f"%{q}%", f"%{q}%"))
            for row in cur.fetchall(): kwic_from_text(row["zu_text"], q, f"EIPC · pair {row['id']}", "EIPC", results, limit)
        if corpus in ("all","ioc"):
            cur.execute("SELECT t.corrected_text, f.filename FROM ioc_transcripts t JOIN ioc_files f ON f.id=t.audio_file_id WHERE t.corrected_text ILIKE %s LIMIT 20", (f"%{q}%",))
            for row in cur.fetchall():
                if row["corrected_text"]: kwic_from_text(row["corrected_text"], q, f"IOC · {row['filename']}", "IOC", results, limit)
        return {"query": q, "total": len(results), "results": results[:limit]}
    finally:
        conn.close()


@app.get("/api/search/frequency")
def word_frequency(corpus: str = Query(default="inc"), top_n: int = Query(default=50, le=200),
                   token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor(); all_text = ""
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
        if not all_text.strip(): return []
        words = re.findall(r'\b[a-zA-ZÀ-ÿ]{2,}\b', all_text.lower())
        total = len(words); freq: dict = {}
        for w in words: freq[w] = freq.get(w, 0) + 1
        top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [{"rank": i+1, "word": w, "frequency": c, "per_million": int(c/total*1_000_000) if total else 0}
                for i, (w, c) in enumerate(top)]
    finally:
        conn.close()


@app.get("/api/search/stats")
def corpus_stats(token: str = Depends(verify_token)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(token_count),0) FROM inc_documents")
        tokens = cur.fetchone()["coalesce"]
        cur.execute("SELECT COUNT(*) FROM eipc_pairs WHERE status='verified'")
        pairs = cur.fetchone()["count"]
        cur.execute("SELECT COUNT(*) FROM ioc_transcripts WHERE is_approved=true")
        transcripts = cur.fetchone()["count"]
        cur.execute("SELECT domain, COALESCE(SUM(token_count),0) as tokens FROM inc_documents GROUP BY domain ORDER BY tokens DESC")
        by_domain = [dict(r) for r in cur.fetchall()]
        return {"total_tokens": int(tokens), "verified_pairs": int(pairs),
                "approved_transcripts": int(transcripts), "by_domain": by_domain}
    finally:
        conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# SERVE FRONTEND
# ══════════════════════════════════════════════════════════════════════════════
index_file = os.path.join(os.path.dirname(__file__), "index.html")

if os.path.exists(index_file):
    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(index_file)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api"): raise HTTPException(404)
        return FileResponse(index_file)