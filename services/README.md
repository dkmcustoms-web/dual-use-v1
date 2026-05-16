# Y901 Sandbox

Standalone research/sandbox app for Y901 dual-use compliance screening.
Built with Streamlit, deployable to Railway, backed by Neon Postgres.

**Purpose:** a separate playground (not production DKM) to gather logic,
test screening flows, and tune the dual-use review process before porting
anything back to the main DKM app.

---

## Features

- **PDF invoice upload** → text extraction → manual or assisted party/product capture → screening
- **Name search** → query OpenSanctions for persons, companies, vessels
- **Product search** → free-text search against Annex I (Reg. 2021/821)
- **Knowledge base browser** → walk the Annex I tree (10 categories, 367+ entries)
- **Data sources page** → upload your own CSVs/JSONs to extend the knowledge base
- **Audit log** → every screening is stored in Postgres with timestamp, inputs, hits, decision

---

## Stack

| Layer | Tech | Why |
|-------|------|-----|
| UI | Streamlit | Same stack as DKM, fast iteration |
| Hosting | Railway | As requested |
| DB | Neon (Postgres) | Free tier serverless, SQL, structured tables |
| Sanctions | OpenSanctions API | Already have key |
| PDF | pdfplumber | Robust text extraction |
| Excel parsing | openpyxl + pandas | For Annex I loader |

> Pinecone was considered but not used. The data here is **structured** (entity records, tree of regulations, screening logs). Pinecone is a vector DB — useful later if you want semantic search over invoice text vs. Annex I descriptions, but unnecessary for v1.

---

## First-time setup

### 1. Neon database

1. Go to https://neon.tech → sign up (free tier is plenty)
2. Create a new project, name it `y901-sandbox`
3. Copy the **connection string** (looks like `postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`)

### 2. OpenSanctions API key

You already have one. Keep it ready as `OPENSANCTIONS_API_KEY`.

### 3. Local development

```bash
git clone <your-repo-url>
cd y901-sandbox

# Create virtual env
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment
cp .env.example .env
# Edit .env: paste DATABASE_URL and OPENSANCTIONS_API_KEY

# Initialize the database (creates tables)
python -m db.init_db

# Load the Annex I Excel (download from DG TRADE first)
python scripts/load_annex_i.py path/to/List_of_dual_use_items_in_Annex_I.xlsx

# Run the app
streamlit run app.py
```

App opens at http://localhost:8501

### 4. Deploy to Railway

1. Push the repo to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub repo
3. Railway auto-detects `railway.toml` and starts the build
4. In Railway → Variables, add:
   - `DATABASE_URL` = your Neon connection string
   - `OPENSANCTIONS_API_KEY` = your key
5. Railway provides a public URL when deploy is done
6. **First deploy:** open Railway's shell or run locally pointing to the prod DB:
   - `python -m db.init_db`
   - `python scripts/load_annex_i.py path/to/excel.xlsx`

---

## Data sources strategy

Every dataset is versioned. The `data_sources` table tracks:
- name, version (e.g. `annex_i_2024_09`)
- type (annex_i, country_risk, manual_note, ...)
- loaded_at (timestamp)
- notes (free text)

When you load new data via the **Data sources** page or scripts, a new row appears here. Screenings reference the active version at the time of the run — this is the audit trail.

**Currently included loaders:**
- Annex I from DG TRADE Excel (script + UI upload)
- Generic CSV/JSON via the Data Sources page (you define columns)

**To add a new source later:** put a loader in `scripts/`, register a new `source_type` in `data_sources`, and add a query module in `services/` if it needs custom logic.

---

## Project layout

```
y901-sandbox/
├── app.py                  # Streamlit home page
├── requirements.txt
├── railway.toml            # Railway deploy config
├── Procfile                # Fallback deploy hint
├── runtime.txt             # Pin Python version
├── .env.example
├── db/
│   ├── connection.py       # SQLAlchemy engine via DATABASE_URL
│   ├── schema.sql          # All table DDL
│   └── init_db.py          # Run schema.sql against the DB
├── services/
│   ├── opensanctions.py    # OpenSanctions API client
│   ├── pdf_extractor.py    # PDF → text
│   ├── annex_i.py          # Annex I queries
│   └── screening.py        # Orchestrate a full screening
├── pages/
│   ├── 1_Upload_invoice.py
│   ├── 2_Search_name.py
│   ├── 3_Search_product.py
│   ├── 4_Knowledge_base.py
│   └── 5_Data_sources.py
└── scripts/
    └── load_annex_i.py     # Excel → Postgres loader
```

---

## Adding more knowledge later

Go to the **Data sources** page in the app. Upload a CSV or JSON. Give it a source name and version. The system stores it in `manual_entries` keyed by source_name, queryable from the **Search product** page.

For richer integration (e.g. when you get the Correlation Table CN→ECN):
1. Add a new table to `db/schema.sql`
2. Add a loader to `scripts/`
3. Add a query function to `services/`
4. Optionally surface it on the screening page

---

## Important caveats

- **Annex I version:** the Excel currently published by DG TRADE is the **September 2024** version. The legally-current version is **November 2025** (Delegated Reg 2025/2003). For audit reproducibility the `regulation_version` field tracks which Annex I version is loaded.
- **Country list:** OpenSanctions covers sanctioned countries via sanctions programmes. For a curated risk-level country list (your `EXP_COUNTRY_RISK` pattern from DKM), build a CSV and upload via Data Sources.
- **Sandbox status:** this is a research/exploration app. Do not put real customer invoices in it without anonymizing them — Railway is public cloud infrastructure.
