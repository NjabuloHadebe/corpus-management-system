# IsiZulu Corpus Management System

A simple web system for cleaning, aligning, transcribing and analysing isiZulu corpora.
Supports INC (text), EIPC (parallel EN/ZU), and IOC (oral/audio).

---

## Project structure

```
izulu_cms/
├── main.py            ← Python backend (FastAPI) — all API routes
├── schema.sql         ← Database tables — paste into Supabase once
├── static/
│   └── index.html     ← Frontend — the full web interface
├── requirements.txt   ← Python packages
├── Procfile           ← Render deployment start command
├── .env.example       ← Environment variable template
└── uploads/           ← Created automatically on first run
    ├── inc/
    ├── eipc/
    └── ioc/
```

---

## Deploy in 4 steps (free, ~20 minutes total)

### Step 1 — Set up the database (Supabase, free)

1. Go to **https://supabase.com** and click **Start your project** (free, no credit card)
2. Create a new project — choose any name (e.g. `izulu-cms`) and a strong password
3. Wait ~2 minutes for the project to start
4. In your Supabase dashboard, go to **SQL Editor** → **New query**
5. Open `schema.sql` from this folder, paste the entire contents, click **Run**
6. You should see "Success. No rows returned" — all tables are created
7. Go to **Settings → Database → Connection string → URI**
8. Copy the full URI — it looks like:
   `postgresql://postgres:[YOUR-PASSWORD]@db.[REF].supabase.co:5432/postgres`
9. Keep this — you need it in Step 3

### Step 2 — Put the code on GitHub (free)

1. Go to **https://github.com** and sign in (or create a free account)
2. Click **New repository** → name it `izulu-cms` → **Create repository**
3. On your computer, open a terminal in this folder and run:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/izulu-cms.git
git push -u origin main
```

### Step 3 — Deploy the backend (Render, free)

1. Go to **https://render.com** and sign in with your GitHub account
2. Click **New** → **Web Service**
3. Connect your `izulu-cms` GitHub repository
4. Set these values:
   - **Name:** `izulu-cms`
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Scroll to **Environment Variables** and add:
   - `DATABASE_URL` → paste the Supabase URI from Step 1
   - `UPLOAD_DIR` → `uploads`
6. Click **Create Web Service**
7. Wait ~3 minutes for the first deploy to finish
8. Render gives you a URL like `https://izulu-cms.onrender.com` — this is your live system

### Step 4 — Connect the frontend to your live backend

1. Open `static/index.html` in a text editor
2. Near the top of the `<script>` section, find this line:

```javascript
var API_BASE = "";
```

3. Change it to your Render URL:

```javascript
var API_BASE = "https://izulu-cms.onrender.com";
```

4. Save the file, then push the change to GitHub:

```bash
git add static/index.html
git commit -m "Set API base URL"
git push
```

Render will automatically redeploy. After ~2 minutes, open your Render URL in a browser — the system is live.

---

## Run locally (for development)

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Copy and fill in your .env
cp .env.example .env
# Edit .env and paste your Supabase DATABASE_URL

# 3. Put index.html in the static folder
mkdir -p static
cp index.html static/

# 4. Start the server
uvicorn main:app --reload

# 5. Open http://localhost:8000 in your browser
```

---

## API endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/dashboard` | Dashboard stats + recent activity |
| GET | `/api/inc/documents` | List INC documents |
| POST | `/api/inc/documents` | Add document (with file upload) |
| POST | `/api/inc/save-text` | Save cleaned text version |
| GET | `/api/inc/documents/{id}/text` | Get latest saved text |
| GET | `/api/inc/stats` | INC totals |
| POST | `/api/eipc/upload-en` | Upload English document |
| POST | `/api/eipc/upload-zu/{doc_id}` | Upload IsiZulu document |
| POST | `/api/eipc/align/{doc_id}` | Auto-align sentences |
| GET | `/api/eipc/pairs` | List pairs (with filter/search/pagination) |
| PATCH | `/api/eipc/pairs/{id}` | Update pair (approve/flag/edit) |
| GET | `/api/eipc/export/csv` | Download all verified pairs as CSV |
| GET | `/api/eipc/export/tmx` | Download as TMX (translation memory) |
| GET | `/api/eipc/stats` | EIPC totals |
| GET | `/api/ioc/files` | List audio files |
| POST | `/api/ioc/upload` | Upload audio file |
| POST | `/api/ioc/transcript` | Save/update transcript |
| GET | `/api/ioc/transcript/{file_id}` | Get transcript for a file |
| GET | `/api/ioc/stats` | IOC totals |
| GET | `/api/search/kwic?q=ukuphila` | KWIC search across corpora |
| GET | `/api/search/frequency?corpus=inc` | Word frequency |
| GET | `/api/search/stats` | Corpus-wide statistics |

Full interactive API docs available at: `https://your-render-url.onrender.com/docs`

---

## Notes

- **Render free tier** sleeps after 15 minutes of inactivity. First request after sleep takes ~30 seconds to wake up. This is normal — just refresh.
- **Supabase free tier** pauses after 1 week of inactivity. Log in to Supabase and click **Resume** to wake it. To prevent pausing, visit the app at least once a week.
- **File uploads** are stored on Render's disk. On the free tier, disk resets on each deploy. For permanent file storage, upgrade to Render's $7/month paid tier, or store files in Supabase Storage (1GB free).
- **Multiple users** can use the system simultaneously — the database handles concurrent writes safely.
