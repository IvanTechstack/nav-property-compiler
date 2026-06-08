# Ivan's Image Optimizer

A Streamlit-based property media management tool for NAV brokerage — optimizes, renames, and uploads listing photos to Cloudflare R2.

## Run & Operate

- Start app: workflow `artifacts/mockup-sandbox: NAV Property Compiler` (port 8000)
- Dev URL: `/__mockup` path on the Replit dev domain
- Streamlit binary: `/home/runner/workspace/.pythonlibs/bin/streamlit` (full path required — Nix has a conflicting old version)
- R2 secrets required: `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- OpenAI: `AI_INTEGRATIONS_OPENAI_BASE_URL` + `AI_INTEGRATIONS_OPENAI_API_KEY` (auto-set via Replit integration) or `OPENAI_API_KEY` for direct API

## Stack

- Python 3.11 + Streamlit 1.57.0
- Pillow 12.2.0 — image processing (resize, WebP encode, EXIF transpose)
- boto3 1.43.11 — Cloudflare R2 (S3-compatible) storage
- openai 2.41.0 — AI content generation via Replit AI Integrations proxy
- No numpy — all image ops use pure PIL; thumbnails served as base64 HTML to avoid numpy C-extension failures in Nix

## Where things live

- `NAV-Property-Compiler/src/nav_property_compiler/main.py` — entire app (single file)
- `NAV-Property-Compiler/pyproject.toml` — Python package definition
- `requirements.txt` (repo root) — pinned deps for Streamlit Community Cloud deploy
- `.streamlit/config.toml` — server config (headless, no port set — port passed via CLI flag)
- `.streamlit/secrets.toml.example` — R2 secrets template for Streamlit Cloud
- `Ivan .png` (repo root) — sidebar avatar; loaded as base64 to avoid numpy
- `artifacts/mockup-sandbox/.replit-artifact/artifact.toml` — workflow + deployment config; PYTHONPATH must be `python3.11/site-packages`

## Architecture decisions

- **Pure PIL, no numpy**: Streamlit's `st.image()` and `st.sidebar.image()` both call numpy internally, which fails in Nix due to missing `libstdc++.so.6`. All images (sidebar avatar, browse thumbnails, upload previews) are encoded to base64 JPEG and injected via `st.markdown()` HTML `<img>` tags.
- **Server-side thumbnails**: Browse page downloads R2 objects server-side, resizes with PIL, and serves as base64 — no CORS config required on the bucket.
- **`st.secrets` + `os.environ` fallback**: `_get_secret()` checks `st.secrets` first (Streamlit Cloud) then `os.environ` (Replit/local) — same codebase works in both environments.
- **Single-file app**: All pages, R2 helpers, and image processors live in `main.py` for simplicity.
- **Full binary path in workflow**: Run command uses `/home/runner/workspace/.pythonlibs/bin/streamlit` to prevent Nix's system streamlit 0.50.2 (Python 3.9) from intercepting.
- **Pure native Streamlit Browse** (June 2026 rewrite): `st.components.v1.html()` was removed in Streamlit 1.57.0. All Browse UI uses `st.columns()` + `st.button()` + `st.checkbox()` + `st.markdown()` only. Zero iframes, zero JavaScript, zero `window.parent` hacks. Navigation is pure `st.session_state` + `st.rerun()`.

## Product

Three-column upload pipeline keyed by property name:
- **Portfolio Gallery** — batch upload, resizes to max 800px height, named `[PREFIX]-01.webp`, `[PREFIX]-02.webp` …
- **Featured Banner** — single image, forced to exactly 1920px wide, archived to `000_MASTER_FEATURED_IMAGES/`
- **Story Cover** — single image or GIF, max 600px wide, named `[PREFIX]-story-cover.[ext]`
- All property assets land in `properties/[PREFIX]/` in the R2 bucket

### Browse page
- **Folder grid** — `st.columns(4)` with one `st.button()` per folder (opens it); `st.checkbox()` styled as selection circle below each card; bulk-delete with two-step confirmation
- **Image grid** — 3-column `st.markdown()` 16:9 thumbnails; `st.checkbox()` per image for selection; Delete Selected pinned to top header; sort radio (Custom/A→Z/Z→A/Date); 📌 Save Order writes `sort_order.json` to R2; ↕ Reverse toggle
- **Settings page** — credential status, connection test, CORS config block

## User preferences

- Corporate palette: Crimson `#990000`, Slate Gray `#708090`, Black `#0d0d0d`
- Sidebar: white background, Ivan avatar (base64 PNG), crimson title, gray divider above nav
- Tone: minimal, premium — no verbose labels or banners

## Gotchas

- **Always use full streamlit binary path** in the workflow run command — `streamlit` on PATH resolves to Nix's 0.50.2 (Python 3.9) which conflicts with our 1.57.0 package
- **Never use `st.image()` or `st.sidebar.image()`** — both crash due to numpy/libstdc++ failure; use base64 HTML `<img>` instead
- **Never use `st.components.v1.html()`** — removed in Streamlit 1.57.0 (June 2026); use native Streamlit primitives only
- **Never use `st.table()`** — uses numpy; use `st.markdown()` with a markdown table instead
- **Never use `window.parent` or `window.top` JS navigation** — iframes no longer used; all routing is `st.session_state` + `st.rerun()`
- The `mockup-sandbox` artifact kind is non-deployable via Replit's publish panel — use Streamlit Community Cloud (see `.streamlit/secrets.toml.example` for secrets setup)
- `Ivan .png` has a space in the filename — keep it exactly as-is (code references that exact path)
- `_safe_key(s)` helper sanitises R2 key paths into valid Streamlit widget keys (`[^a-zA-Z0-9_-]` → `_`)

## Browse page state model

- `st.session_state["browse_open_folder"]` — currently open folder path (e.g. `properties/123-main-st/`); absent = show folder grid
- `st.session_state["sel_folder_{safe_key}"]` — checkbox state per folder card (bool)
- `st.session_state["sel_img_{safe_key}"]` — checkbox state per image (bool)
- `st.session_state["thumbs_{folder}"]` — dict of `{key: base64_jpeg}` thumbnail cache per folder
- `st.session_state["browse_sort"]` — current sort mode radio value
- `st.session_state["browse_reversed"]` — bool, ↕ reverse toggle
- `st.session_state["confirm_bulk_delete"]` — list of folders pending bulk deletion confirm
- `st.session_state["confirm_wipe"]` — folder path pending wipe confirm

## Deployment

- See `DEPLOY.md` for the step-by-step Streamlit Community Cloud setup walkthrough
- Repo: `IvanTechstack/nav-property-compiler`, main file: `NAV-Property-Compiler/src/nav_property_compiler/main.py`
- Secrets format: `.streamlit/secrets.toml.example`

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
