# Ivan's Image Optimizer

A Streamlit-based property media management tool for NAV brokerage — optimizes, renames, and uploads listing photos to Cloudflare R2.

## Run & Operate

- Start app: workflow `artifacts/mockup-sandbox: NAV Property Compiler` (port 8000)
- Dev URL: `/__mockup` path on the Replit dev domain
- Streamlit binary: `/home/runner/workspace/.pythonlibs/bin/streamlit` (full path required — Nix has a conflicting old version)
- R2 secrets required: `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`

## Stack

- Python 3.11 + Streamlit 1.57.0
- Pillow 12.2.0 — image processing (resize, WebP encode, EXIF transpose)
- boto3 1.43.11 — Cloudflare R2 (S3-compatible) storage
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

## Product

Three-column upload pipeline keyed by property name:
- **Portfolio Gallery** — batch upload, resizes to max 800px height, named `[PREFIX]-01.webp`, `[PREFIX]-02.webp` …
- **Featured Banner** — single image, forced to exactly 1920px wide, named `[PREFIX]-banner.webp`
- **Story Cover** — single image or GIF, max 600px wide, named `[PREFIX]-story-cover.[ext]`
- All assets land in `properties/[PREFIX]/` in the R2 bucket
- **Browse page** — folder selectbox filters by property, 3-column thumbnail grid, download + delete actions
- **Settings page** — credential status, connection test, CORS config block

## User preferences

- Corporate palette: Crimson `#990000`, Slate Gray `#708090`, Black `#0d0d0d`
- Sidebar: white background, Ivan avatar (base64 PNG), crimson title, gray divider above nav
- Tone: minimal, premium — no verbose labels or banners

## Gotchas

- **Always use full streamlit binary path** in the workflow run command — `streamlit` on PATH resolves to Nix's 0.50.2 (Python 3.9) which conflicts with our 1.57.0 package
- **Never use `st.image()` or `st.sidebar.image()`** — both crash due to numpy/libstdc++ failure; use base64 HTML `<img>` instead
- **`st.table()` also uses numpy** — use `st.markdown()` with a markdown table instead
- The `mockup-sandbox` artifact kind is non-deployable via Replit's publish panel — use Streamlit Community Cloud (see `.streamlit/secrets.toml.example` for secrets setup)
- `Ivan .png` has a space in the filename — keep it exactly as-is (code references that exact path)

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
