# Deploying to Streamlit Community Cloud

The app runs on Replit during development. For a permanent public URL, deploy to
[Streamlit Community Cloud](https://share.streamlit.io) — it is free and reads
directly from the GitHub repo.

## Prerequisites

- GitHub repo: **IvanTechstack/nav-property-compiler** (must be public or the
  Streamlit Cloud account must have access)
- Your three Cloudflare R2 credentials:
  - `R2_ENDPOINT_URL` — e.g. `https://<account-id>.r2.cloudflarestorage.com`
  - `R2_ACCESS_KEY_ID`
  - `R2_SECRET_ACCESS_KEY`

## Step-by-step

### 1. Create a new app on Streamlit Cloud

Go to **https://share.streamlit.io** → click **New app**.

| Field | Value |
|---|---|
| Repository | `IvanTechstack/nav-property-compiler` |
| Branch | `main` |
| Main file path | `NAV-Property-Compiler/src/nav_property_compiler/main.py` |

### 2. Add secrets before deploying

Before clicking **Deploy**, open **Advanced settings → Secrets** and paste:

```toml
R2_ENDPOINT_URL = "https://<account-id>.r2.cloudflarestorage.com"
R2_ACCESS_KEY_ID = "<your-r2-access-key-id>"
R2_SECRET_ACCESS_KEY = "<your-r2-secret-access-key>"
```

Replace the placeholder values with your real R2 credentials. The format matches
`.streamlit/secrets.toml.example` — do not commit real values there.

### 3. Deploy

Click **Deploy**. Streamlit Cloud will:

1. Clone the repo
2. Install dependencies from `requirements.txt` (root of repo)
3. Start the app at a URL like `https://ivantech-nav-property-compiler-<hash>.streamlit.app`

First boot takes ~2 minutes while packages install.

### 4. Verify

Once live, open the app and go to **Browse** — the property folder list should
load from R2 and thumbnails should render in the 3-column grid. If the folder
list is empty but no error appears, the bucket is empty (expected on a fresh
account).

If credentials are wrong you will see an error on the Browse or Settings page —
go to **App settings → Secrets** in Streamlit Cloud to correct the values (no
redeploy required, the app hot-reloads secrets).

## Updating the app after code changes

Push commits to the `main` branch. Streamlit Cloud automatically redeploys within
a few minutes. No manual trigger is needed.

## Secrets reference

See `.streamlit/secrets.toml.example` for the canonical secrets format.
The app reads secrets via `st.secrets` on Streamlit Cloud and falls back to
`os.environ` when running locally on Replit.
