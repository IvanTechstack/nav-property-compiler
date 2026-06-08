"""Ivan's Image Optimizer — Property media management via Cloudflare R2."""

from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass

import boto3
import streamlit as st
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "Ivan's Image Optimizer"
BUCKET_NAME = "nav-property-media"

MASTER_FEATURED_PREFIX = "000_MASTER_FEATURED_IMAGES"

GALLERY_MAX_HEIGHT = 800
BANNER_WIDTH = 1920
STORY_COVER_MAX_WIDTH = 600

DEFAULT_QUALITY = 82
DOWNLOAD_EXPIRY = 3600

SUPPORTED_UPLOAD_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp", "gif"]
SUPPORTED_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "tiff": "image/tiff", "bmp": "image/bmp", "gif": "image/gif",
}
GALLERY_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"]

CRIMSON = "#990000"
SLATE = "#708090"
BLACK = "#0d0d0d"

_IVAN_IMG_PATH = "/home/runner/workspace/Ivan .png"

R2_CORS_CONFIG = [
    {
        "AllowedOrigins": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedHeaders": ["Authorization", "Content-Type", "Range"],
        "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
        "MaxAgeSeconds": 300,
    }
]


# ---------------------------------------------------------------------------
# R2 client
# ---------------------------------------------------------------------------

@dataclass
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str = BUCKET_NAME


def _get_secret(key: str) -> str:
    try:
        val = st.secrets.get(key, "")
        if val:
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get(key, "").strip()


def _load_r2_config() -> R2Config:
    endpoint = _get_secret("R2_ENDPOINT_URL")
    key_id   = _get_secret("R2_ACCESS_KEY_ID")
    secret   = _get_secret("R2_SECRET_ACCESS_KEY")
    missing = [k for k, v in {
        "R2_ENDPOINT_URL": endpoint,
        "R2_ACCESS_KEY_ID": key_id,
        "R2_SECRET_ACCESS_KEY": secret,
    }.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing secrets/env vars: {', '.join(missing)}")
    return R2Config(endpoint_url=endpoint, access_key_id=key_id, secret_access_key=secret)


@st.cache_resource(show_spinner=False)
def get_r2_client():
    cfg = _load_r2_config()
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------

def _to_web_mode(img: Image.Image, fmt: str) -> Image.Image:
    if fmt == "WEBP":
        return img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img
    return img.convert("RGB") if img.mode != "RGB" else img


def _encode(img: Image.Image, fmt: str, quality: int) -> tuple[bytes, str]:
    buf = io.BytesIO()
    kwargs: dict = {"quality": quality, "optimize": True}
    if fmt == "WEBP":
        kwargs["method"] = 6
    img.save(buf, format=fmt, **kwargs)
    buf.seek(0)
    return buf.read(), "webp" if fmt == "WEBP" else fmt.lower()


def process_gallery(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY,
                    output_format: str = "WEBP") -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    if h > GALLERY_MAX_HEIGHT:
        img = img.resize((int(w * GALLERY_MAX_HEIGHT / h), GALLERY_MAX_HEIGHT), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_banner(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY,
                   output_format: str = "WEBP") -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    img = img.resize((BANNER_WIDTH, int(h * BANNER_WIDTH / w)), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_story_cover(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY,
                        src_ext: str = "") -> tuple[bytes, str]:
    if src_ext == "gif":
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
        if w > STORY_COVER_MAX_WIDTH:
            img = img.resize((STORY_COVER_MAX_WIDTH, int(h * STORY_COVER_MAX_WIDTH / w)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        return buf.read(), "gif"
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, "WEBP")
    w, h = img.size
    if w > STORY_COVER_MAX_WIDTH:
        img = img.resize((STORY_COVER_MAX_WIDTH, int(h * STORY_COVER_MAX_WIDTH / w)), Image.LANCZOS)
    return _encode(img, "WEBP", quality)


# ---------------------------------------------------------------------------
# R2 helpers
# ---------------------------------------------------------------------------

def list_objects(prefix: str = "") -> list[dict]:
    paginator = get_r2_client().get_paginator("list_objects_v2")
    out: list[dict] = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
        out.extend(page.get("Contents", []))
    return out


def upload_object(key: str, data: bytes, content_type: str) -> None:
    get_r2_client().put_object(Bucket=BUCKET_NAME, Key=key, Body=data, ContentType=content_type)


def download_object(key: str) -> bytes:
    return get_r2_client().get_object(Bucket=BUCKET_NAME, Key=key)["Body"].read()


def delete_object(key: str) -> None:
    get_r2_client().delete_object(Bucket=BUCKET_NAME, Key=key)


def presigned_url(key: str, expires_in: int = DOWNLOAD_EXPIRY) -> str:
    return get_r2_client().generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET_NAME, "Key": key}, ExpiresIn=expires_in
    )


def _next_gallery_seq(prefix: str) -> int:
    existing = list_objects(prefix=f"properties/{prefix}/")
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.webp$")
    for obj in existing:
        m = pattern.match(obj["Key"].split("/")[-1])
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def _load_sort_order(folder: str) -> list[str] | None:
    try:
        data = download_object(f"{folder}sort_order.json")
        return json.loads(data).get("order")
    except Exception:
        return None


def _save_sort_order(folder: str, ordered_keys: list[str]) -> None:
    upload_object(
        f"{folder}sort_order.json",
        json.dumps({"order": ordered_keys}).encode(),
        "application/json",
    )


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _safe_key(s: str) -> str:
    """Sanitise an arbitrary string for use as a Streamlit widget key."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _is_image(key: str) -> bool:
    return key.lower().rsplit(".", 1)[-1] in SUPPORTED_UPLOAD_TYPES


def _thumbnail_b64(key: str, max_w: int = 400) -> str | None:
    """Server-side thumbnail → base64 JPEG. Pure PIL, no numpy."""
    import base64
    try:
        raw = download_object(key)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        img = img.convert("RGB") if img.mode != "RGB" else img
        w, h = img.size
        if w > max_w:
            img = img.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _inject_css() -> None:
    st.markdown(f"""
<style>
/* ── Global primary buttons ──────────────────────────────── */
div.stButton > button[kind="primary"],
div.stFormSubmitButton > button[kind="primary"] {{
    background-color:{CRIMSON}!important;
    border-color:{CRIMSON}!important;color:#fff!important;
}}
div.stButton > button[kind="primary"]:hover,
div.stFormSubmitButton > button[kind="primary"]:hover {{
    background-color:#7a0000!important;border-color:#7a0000!important;
}}

/* ── Sidebar ─────────────────────────────────────────────── */
section[data-testid="stSidebar"]{{
    background-color:#ffffff!important;border-right:3px solid {CRIMSON};
}}
section[data-testid="stSidebar"] > div{{background-color:#ffffff!important}}
.sidebar-divider{{border:none;border-top:1px solid #d9d9d9;margin:.75rem 0 1rem}}

/* ─────────────────────────────────────────────────────────
   MAC FINDER FOLDER CARDS
   Each card uses two wrappers:
     div.finder-card        (standard property folder)
     div.finder-card-master (⭐ Master Featured archive)

   Architecture: the card border/background lives on the
   wrapper div; the st.button() inside is transparent so
   the whole card is one clickable surface.
   ─────────────────────────────────────────────────────── */

/* Card shell */
div.finder-card,
div.finder-card-master {{
    border:1.5px solid #eaeaea;
    border-radius:16px;
    background:#fafafa;
    padding:18px 10px 12px;
    text-align:center;
    transition:transform .18s ease,box-shadow .18s ease,
               border-color .18s ease,background-color .18s ease;
    margin-bottom:.2rem;
    cursor:pointer;
}}
div.finder-card:hover {{
    transform:translateY(-5px);
    box-shadow:0 10px 30px rgba(0,0,0,.10);
    border-color:#c0c0c0;
    background:#fff;
}}
div.finder-card-master {{
    background:#fff9f9;border-color:#f0d0d0;
}}
div.finder-card-master:hover {{
    transform:translateY(-5px);
    box-shadow:0 10px 30px rgba(153,0,0,.12);
    border-color:#d08080;background:#fff5f5;
}}

/* Emoji icon row inside each card */
div.finder-card-icon {{
    font-size:2.8rem;line-height:1.25;
    margin-bottom:.3rem;pointer-events:none;
}}

/* The st.button() inside the card: transparent, just the name */
div.finder-card button,
div.finder-card-master button {{
    background:transparent!important;
    border:none!important;box-shadow:none!important;
    padding:.15rem 4px .1rem!important;
    min-height:unset!important;height:auto!important;
    font-size:.9rem!important;font-weight:600!important;
    color:#1a1a1a!important;width:100%!important;
    line-height:1.3!important;
    transition:none!important;
}}
div.finder-card button:hover,
div.finder-card-master button:hover {{
    background:transparent!important;
    text-decoration:underline!important;
    transform:none!important;box-shadow:none!important;
}}
div.finder-card button p,
div.finder-card-master button p {{
    text-align:center!important;margin:0!important;
    white-space:normal!important;word-break:break-word!important;
}}

/* Folder meta line (file count + size) rendered below button */
div.finder-card-meta {{
    font-size:.72rem;color:#aaa;
    padding:.1rem 0 .3rem;pointer-events:none;
}}

/* ── Folder selection checkboxes — circle style ─────────── */
div.sel-circle [data-testid="stCheckbox"] {{
    display:flex!important;justify-content:center!important;
    margin-top:.3rem!important;
}}
div.sel-circle [data-testid="stCheckbox"] input[type="checkbox"] {{
    width:20px!important;height:20px!important;
    border-radius:50%!important;cursor:pointer!important;
    accent-color:{CRIMSON};
}}
div.sel-circle [data-testid="stCheckbox"] label {{display:none!important}}

/* ── Image select-toggle button ──────────────────────────── */
div.img-sel-btn button {{
    min-height:unset!important;height:auto!important;
    padding:.3rem 0!important;
    font-size:.75rem!important;
    border-radius:0 0 10px 10px!important;
    margin-top:-1px!important;
    width:100%!important;
    transition:background-color .15s,color .15s!important;
}}

/* ── Image position number inputs ────────────────────────── */
div.img-order-input [data-testid="stNumberInput"] {{
    margin-top:.2rem!important;
}}
div.img-order-input [data-testid="stNumberInput"] input {{
    text-align:center!important;font-size:.78rem!important;
    padding:.25rem .3rem!important;
}}
div.img-order-input label {{display:none!important}}

/* ── Action bar ──────────────────────────────────────────── */
div.action-bar {{
    background:#f6f6f6;border:1px solid #ebebeb;border-radius:10px;
    padding:.5rem .75rem;margin-bottom:.75rem;
    display:flex;align-items:center;gap:.6rem;
}}

/* ── Upload column boxes ─────────────────────────────────── */
.upload-col{{border:1px solid {SLATE};border-radius:8px;padding:1rem}}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# OpenAI helpers
# ---------------------------------------------------------------------------

_AI_SYSTEM = (
    "You are a luxury real estate copywriter for NAV brokerage. "
    "Return ONLY valid JSON — no markdown fences, no commentary."
)

_AI_PROMPT = """\
Parse the MLS listing below and return a single JSON object with these exact keys:

{{
  "stats": {{
    "mls": "MLS number",
    "address": "full street address",
    "city": "City, ST",
    "beds": "bedrooms",
    "baths": "bathrooms e.g. 3.5",
    "sqft": "square footage with commas",
    "year": "year built",
    "price": "$X,XXX,XXX"
  }},
  "full_description": "3 rich marketing paragraphs each wrapped in <p> tags. Specific architecture, interiors, finishes.",
  "neighborhood": "1-2 paragraphs <p> about location, walkability, schools, amenities.",
  "lifestyle": "1-2 paragraphs <p> about the lifestyle: entertaining, family, outdoor living.",
  "agent_bio": "1 professional closing paragraph <p> from NAV brokerage mentioning the address.",
  "bullets_24": ["exactly 24 concise property feature bullets, each 3-8 words"],
  "flyer_bullets": ["exactly 6 compelling one-line bullets for a print flyer"],
  "social_post": "Instagram/Facebook caption with emojis and hashtags at end (plain text, use newlines)"
}}

Property: {property_name}
Studeo.ai URL: {studeo_url}

MLS TEXT:
{mls_text}
"""


def _get_openai_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed — add it to requirements.txt")
    base_url = _get_secret("AI_INTEGRATIONS_OPENAI_BASE_URL") or None
    api_key = (
        _get_secret("AI_INTEGRATIONS_OPENAI_API_KEY")
        or _get_secret("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "No OpenAI key found. Set OPENAI_API_KEY or connect the Replit OpenAI integration."
        )
    kw: dict = {"api_key": api_key}
    if base_url:
        kw["base_url"] = base_url
    return OpenAI(**kw)


def _compile_listing_ai(property_name: str, mls_text: str, studeo_url: str) -> dict:
    client = _get_openai_client()
    msg = _AI_PROMPT.format(
        property_name=property_name or "(not provided)",
        studeo_url=studeo_url or "(not provided)",
        mls_text=mls_text.strip(),
    )
    resp = client.chat.completions.create(
        model="gpt-5.4",
        messages=[
            {"role": "system", "content": _AI_SYSTEM},
            {"role": "user", "content": msg},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=8192,
    )
    return json.loads(resp.choices[0].message.content or "{}")


# ---------------------------------------------------------------------------
# Upload sequence reorder panel
# ---------------------------------------------------------------------------

def _render_upload_sequence(prefix: str) -> None:
    """Show gallery sequence reorder panel for the most recently uploaded batch."""
    if not prefix:
        return
    if st.session_state.get("last_gallery_prefix") != prefix:
        return
    keys: list[str] = st.session_state.get("last_gallery_keys", [])
    if not keys:
        return
    thumbs: dict[str, str | None] = st.session_state.get("last_gallery_thumbs", {})

    st.markdown(
        "<div style='margin-top:1.4rem'></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='font-size:.8rem;font-weight:700;color:#aaa;"
        f"text-transform:uppercase;letter-spacing:.07em'>"
        f"Gallery Sequence — {prefix} ({len(keys)} photos)</div>",
        unsafe_allow_html=True,
    )

    # Control row
    ctrl_a, ctrl_b, ctrl_c, ctrl_d = st.columns([2, 2, 2, 6])
    with ctrl_a:
        if st.button("↕ Reverse", key="seq_reverse", use_container_width=True):
            st.session_state["last_gallery_keys"] = keys[::-1]
            folder = f"properties/{prefix}/"
            _save_sort_order(folder, st.session_state["last_gallery_keys"])
            st.rerun()
    with ctrl_b:
        if st.button("📌 Save Order", key="seq_save", use_container_width=True):
            order_pairs = [
                (int(st.session_state.get(f"seq_ord_{_safe_key(k)}", i + 1)), i, k)
                for i, k in enumerate(keys)
            ]
            order_pairs.sort(key=lambda x: (x[0], x[1]))
            sorted_keys = [k for _, _, k in order_pairs]
            st.session_state["last_gallery_keys"] = sorted_keys
            folder = f"properties/{prefix}/"
            _save_sort_order(folder, sorted_keys)
            for k in keys:
                st.session_state.pop(f"seq_ord_{_safe_key(k)}", None)
            st.success("Gallery order saved to R2.", icon="📌")
            st.rerun()
    with ctrl_c:
        if st.button("✕ Clear", key="seq_clear", use_container_width=True):
            st.session_state.pop("last_gallery_keys", None)
            st.session_state.pop("last_gallery_thumbs", None)
            st.session_state.pop("last_gallery_prefix", None)
            st.rerun()

    # Thumbnail grid (4 per row)
    st.markdown("<div style='margin:.5rem 0'></div>", unsafe_allow_html=True)
    COLS = 4
    for row_start in range(0, len(keys), COLS):
        row_keys = keys[row_start:row_start + COLS]
        cols = st.columns(COLS)
        for col, k in zip(cols, row_keys):
            safe_k = _safe_key(k)
            fname = k.split("/")[-1]
            b64 = thumbs.get(k)
            with col:
                if b64:
                    st.markdown(
                        f"<div style='border:1.5px solid #eee;border-radius:8px;"
                        f"overflow:hidden;margin-bottom:.15rem'>"
                        f"<div style='position:relative;padding-bottom:56.25%;"
                        f"background:#e8e8e8'>"
                        f"<img src='data:image/jpeg;base64,{b64}' "
                        f"style='position:absolute;top:0;left:0;width:100%;height:100%;"
                        f"object-fit:cover'></div>"
                        f"<div style='font-size:.6rem;color:#999;padding:2px 5px;"
                        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>"
                        f"{fname}</div></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"<div style='border:1.5px solid #eee;border-radius:8px;"
                        f"padding:1rem;text-align:center;color:#ccc'>{fname}</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown("<div class='img-order-input'>", unsafe_allow_html=True)
                row_idx = keys.index(k)
                st.number_input(
                    "Position",
                    min_value=1,
                    max_value=len(keys),
                    value=row_idx + 1,
                    step=1,
                    key=f"seq_ord_{safe_k}",
                    label_visibility="collapsed",
                )
                st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# HTML template generators
# ---------------------------------------------------------------------------

def _for_sale_html(prefix: str, data: dict, studeo_url: str) -> str:
    stats = data.get("stats", {})
    address = stats.get("address", prefix)
    city    = stats.get("city", "")
    price   = stats.get("price", "")
    beds    = stats.get("beds", "")
    baths   = stats.get("baths", "")
    sqft    = stats.get("sqft", "")
    year    = stats.get("year", "")
    mls_no  = stats.get("mls", "")

    # Gallery images from R2 (sorted, 7-day presigned)
    folder = f"properties/{prefix}/"
    try:
        objs = list_objects(prefix=folder)
        img_keys = [
            o["Key"] for o in objs
            if _is_image(o["Key"]) and "story-cover" not in o["Key"]
        ]
        saved = _load_sort_order(folder)
        if saved:
            s = set(img_keys)
            img_keys = [k for k in saved if k in s] + [k for k in img_keys if k not in set(saved)]
        gallery_urls = [presigned_url(k, expires_in=604800) for k in img_keys]
    except Exception:
        gallery_urls = []

    # Featured banner
    try:
        banner_url = presigned_url(f"{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp", expires_in=604800)
    except Exception:
        banner_url = ""

    # Gallery HTML (3-col grid)
    if gallery_urls:
        gallery_items = "\n".join(
            f'  <a href="{u}" target="_blank"><img src="{u}" alt="Photo {i+1}" loading="lazy"></a>'
            for i, u in enumerate(gallery_urls)
        )
        gallery_section = (
            "<section class='gallery'>\n"
            f"{gallery_items}\n"
            "</section>"
        )
    else:
        gallery_section = "<section class='gallery'><p style='padding:2rem;color:#aaa'>Gallery images will appear here after upload.</p></section>"

    # 24 feature bullets
    bullets = data.get("bullets_24", [])
    bullets_html = "".join(
        f"<div class='feat'><span class='dot'>&#9679;</span>{b}</div>"
        for b in bullets[:24]
    )

    # CSS radio tabs: Description / Neighborhood / Lifestyle / Agent
    tabs_content = {
        "Description":   data.get("full_description", ""),
        "Neighborhood":  data.get("neighborhood", ""),
        "Lifestyle":     data.get("lifestyle", ""),
        "Agent":         data.get("agent_bio", ""),
    }
    tab_inputs = ""
    tab_labels = ""
    tab_panels = ""
    tab_css_show = ""
    for i, (name, content) in enumerate(tabs_content.items(), 1):
        checked = "checked" if i == 1 else ""
        tab_inputs += f'<input type="radio" name="tab" id="t{i}" {checked}>\n'
        tab_labels += f'<label for="t{i}">{name}</label>\n'
        tab_panels += f'<div class="panel" id="p{i}">{content}</div>\n'
        tab_css_show += f"#t{i}:checked~.tab-labels label[for='t{i}']{{color:#990000;border-bottom:3px solid #990000;}}\n"
        tab_css_show += f"#t{i}:checked~.tab-body #p{i}{{display:block;}}\n"

    map_query = city.replace(" ", "+").replace(",", "%2C")
    map_src = f"https://maps.google.com/maps?q={map_query}&output=embed"

    css = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Helvetica Neue',Arial,sans-serif;background:#fff;color:#1a1a1a;font-size:16px}
a{color:#990000;text-decoration:none}
a:hover{text-decoration:underline}

/* ── Hero ── */
.hero{width:100%;max-height:540px;overflow:hidden;position:relative}
.hero img{width:100%;height:540px;object-fit:cover}
.hero-overlay{position:absolute;bottom:0;left:0;right:0;padding:2rem 2.5rem;
  background:linear-gradient(transparent,rgba(0,0,0,.65))}

/* ── Desktop stats bar ── */
.stat-bar{background:#0d0d0d;color:#fff;padding:.85rem 2.5rem;
  display:flex;align-items:center;gap:2.5rem;flex-wrap:wrap;
  border-bottom:3px solid #990000}
.stat-bar .s{display:flex;align-items:center;gap:.45rem;font-size:.9rem}
.stat-bar .s svg{flex-shrink:0}
.stat-bar .label{font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;color:#888;display:block}
.stat-bar .val{font-weight:700;font-size:1rem}
.price-chip{margin-left:auto;background:#990000;color:#fff;padding:.5rem 1.25rem;
  border-radius:24px;font-weight:700;font-size:1.1rem;white-space:nowrap}
.mls-tag{margin-left:.5rem;font-size:.72rem;color:#666;align-self:flex-end;padding-bottom:.15rem}

/* ── Mobile header (hidden desktop) ── */
.mobile-header{display:none;padding:1.25rem 1rem;border-bottom:3px solid #990000}
.mobile-header h1{font-size:1.3rem;font-weight:800;color:#0d0d0d;margin-bottom:.5rem}
.mobile-stats{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem}
.mobile-stats span{background:#f4f4f4;border-radius:20px;padding:.25rem .75rem;font-size:.8rem;font-weight:600}
.mobile-price{font-size:1.4rem;font-weight:900;color:#990000}

@media(max-width:768px){
  .stat-bar{display:none}
  .mobile-header{display:block}
  .hero img{height:260px}
}

/* ── Gallery grid ── */
.gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin:1.5rem 0}
.gallery a{display:block;overflow:hidden;aspect-ratio:4/3}
.gallery img{width:100%;height:100%;object-fit:cover;transition:transform .3s}
.gallery a:hover img{transform:scale(1.04)}
@media(max-width:600px){.gallery{grid-template-columns:repeat(2,1fr)}}

/* ── Feature bullets 24 ── */
.features{padding:2rem 2.5rem;background:#fafafa}
.features h2{font-size:1.1rem;font-weight:800;text-transform:uppercase;letter-spacing:.07em;
  color:#990000;margin-bottom:1.2rem;border-bottom:2px solid #990000;padding-bottom:.4rem}
.feat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.55rem}
.feat{font-size:.82rem;color:#333;display:flex;align-items:flex-start;gap:.4rem;line-height:1.35}
.dot{color:#990000;flex-shrink:0;font-size:.6rem;margin-top:.2rem}
@media(max-width:700px){.feat-grid{grid-template-columns:repeat(2,1fr)}}

/* ── CSS Radio Tabs ── */
.tabs-wrap{padding:2rem 2.5rem}
.tabs-wrap input[type=radio]{display:none}
.tab-labels{display:flex;gap:0;border-bottom:2px solid #eaeaea;margin-bottom:1.5rem}
.tab-labels label{padding:.6rem 1.4rem;font-size:.88rem;font-weight:600;cursor:pointer;
  color:#708090;border-bottom:3px solid transparent;margin-bottom:-2px;transition:color .15s}
.tab-labels label:hover{color:#990000}
.panel{display:none;line-height:1.8;color:#333;font-size:.95rem}
.panel p{margin-bottom:1rem}

/* ── Studeo split ── */
.studeo{display:grid;grid-template-columns:1fr 1fr;align-items:center;gap:2rem;
  padding:2.5rem;background:#0d0d0d;color:#fff}
.studeo h3{font-size:1.3rem;font-weight:800;color:#fff;margin-bottom:.6rem}
.studeo p{color:#aaa;font-size:.9rem;line-height:1.7;margin-bottom:1.2rem}
.studeo-btn{display:inline-block;background:#990000;color:#fff;padding:.7rem 1.8rem;
  border-radius:6px;font-weight:700;font-size:.9rem;transition:background .2s}
.studeo-btn:hover{background:#7a0000;text-decoration:none}
.studeo-right{text-align:center}
.studeo-icon{font-size:4rem;margin-bottom:.75rem;display:block}
@media(max-width:700px){.studeo{grid-template-columns:1fr}.studeo-right{display:none}}

/* ── Map ── */
.map-wrap{height:400px;overflow:hidden}
.map-wrap iframe{width:100%;height:100%;border:0}

/* ── Footer ── */
.footer{padding:2rem 2.5rem;border-top:3px solid #990000;
  background:#fff;font-size:.82rem;color:#708090;line-height:1.7}
.footer strong{color:#0d0d0d}
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{address}</title>
<style>{css}
{tab_css_show}
</style>
</head>
<body>

<!-- Hero Banner -->
{'<div class="hero"><img src="' + banner_url + '" alt="' + address + '"><div class="hero-overlay"></div></div>' if banner_url else ''}

<!-- Desktop Stats Bar -->
<div class="stat-bar">
  <div class="s">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#990000" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
    <div><span class="label">Beds</span><span class="val">{beds}</span></div>
  </div>
  <div class="s">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#990000" stroke-width="2"><path d="M4 12h16M4 12V6a2 2 0 012-2h12a2 2 0 012 2v6M4 12v6a2 2 0 002 2h12a2 2 0 002-2v-6"/></svg>
    <div><span class="label">Baths</span><span class="val">{baths}</span></div>
  </div>
  <div class="s">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#990000" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>
    <div><span class="label">Living Area</span><span class="val">{sqft} sq ft</span></div>
  </div>
  <div class="s">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#990000" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
    <div><span class="label">Year Built</span><span class="val">{year}</span></div>
  </div>
  <span class="price-chip">{price}</span>
  <span class="mls-tag">MLS# {mls_no}</span>
</div>

<!-- Mobile Header -->
<div class="mobile-header">
  <h1>{address}</h1>
  <div class="mobile-stats">
    <span>&#127970; {beds} Beds</span>
    <span>&#128704; {baths} Baths</span>
    <span>&#128204; {sqft} sq ft</span>
    <span>&#128197; Built {year}</span>
  </div>
  <div class="mobile-price">{price}</div>
</div>

<!-- Gallery Matrix -->
{gallery_section}

<!-- Feature Bullets -->
<section class="features">
  <h2>Property Highlights</h2>
  <div class="feat-grid">
{bullets_html}
  </div>
</section>

<!-- CSS Radio Tabs -->
<div class="tabs-wrap">
{tab_inputs}
<div class="tab-labels">
{tab_labels}
</div>
<div class="tab-body">
{tab_panels}
</div>
</div>

<!-- Studeo.ai Booklet -->
<div class="studeo">
  <div class="studeo-left">
    <h3>Explore the Interactive Booklet</h3>
    <p>Step inside {address} with our immersive Studeo.ai digital presentation — curated photography, floor plans, and neighbourhood insights in one seamless experience.</p>
    <a class="studeo-btn" href="{studeo_url or '#'}" target="_blank">View Booklet &#8594;</a>
  </div>
  <div class="studeo-right">
    <span class="studeo-icon">&#128218;</span>
    <div style="font-size:.8rem;color:#aaa">Scan or click to explore</div>
  </div>
</div>

<!-- Google Map -->
<div class="map-wrap">
  <iframe src="{map_src}" loading="lazy" allowfullscreen></iframe>
</div>

<!-- Footer -->
<footer class="footer">
  <strong>NAV Brokerage</strong> &nbsp;|&nbsp; {address} &nbsp;|&nbsp; MLS# {mls_no}<br>
  {data.get('agent_bio', '')}
</footer>

</body>
</html>"""
    return html


def _sold_html(prefix: str, data: dict) -> str:
    stats = data.get("stats", {})
    address = stats.get("address", prefix)
    price   = stats.get("price", "")
    beds    = stats.get("beds", "")
    baths   = stats.get("baths", "")
    sqft    = stats.get("sqft", "")
    year    = stats.get("year", "")
    city    = stats.get("city", "")

    try:
        banner_url = presigned_url(f"{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp", expires_in=604800)
    except Exception:
        banner_url = ""

    css = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Helvetica Neue',Arial,sans-serif;background:#0d0d0d;color:#fff}
.hero{position:relative;width:100%;height:520px;overflow:hidden}
.hero img{width:100%;height:100%;object-fit:cover;filter:brightness(.55)}
.sold-badge{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  text-align:center}
.sold-badge .word{font-size:6rem;font-weight:900;color:#990000;line-height:1;
  letter-spacing:.04em;text-shadow:0 4px 40px rgba(0,0,0,.8)}
.sold-badge .addr{font-size:1.4rem;color:#fff;margin-top:.75rem;font-weight:600}
@media(max-width:600px){.sold-badge .word{font-size:3.5rem}.hero{height:320px}}
.stats-row{background:#1a1a1a;border-top:4px solid #990000;
  display:flex;justify-content:center;gap:3rem;padding:1.5rem;flex-wrap:wrap}
.stat{text-align:center}
.stat .lbl{font-size:.65rem;text-transform:uppercase;letter-spacing:.07em;color:#708090}
.stat .val{font-size:1.3rem;font-weight:800;color:#fff;margin-top:.15rem}
.content{max-width:820px;margin:3rem auto;padding:0 1.5rem;color:#ccc;
  line-height:1.85;font-size:.97rem}
.content h2{color:#990000;font-size:1.5rem;font-weight:800;margin-bottom:1.2rem;
  text-transform:uppercase;letter-spacing:.04em}
.content p{margin-bottom:1.1rem}
.footer{text-align:center;padding:2rem;border-top:2px solid #990000;
  color:#708090;font-size:.82rem;margin-top:2rem}
"""

    hero_img = (
        f'<img src="{banner_url}" alt="{address}">'
        if banner_url
        else '<div style="width:100%;height:100%;background:#222"></div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SOLD — {address}</title>
<style>{css}</style>
</head>
<body>

<div class="hero">
  {hero_img}
  <div class="sold-badge">
    <div class="word">SOLD</div>
    <div class="addr">{address}</div>
  </div>
</div>

<div class="stats-row">
  <div class="stat"><div class="lbl">Sale Price</div><div class="val">{price}</div></div>
  <div class="stat"><div class="lbl">Beds</div><div class="val">{beds}</div></div>
  <div class="stat"><div class="lbl">Baths</div><div class="val">{baths}</div></div>
  <div class="stat"><div class="lbl">Living Area</div><div class="val">{sqft} sq ft</div></div>
  <div class="stat"><div class="lbl">Year Built</div><div class="val">{year}</div></div>
  <div class="stat"><div class="lbl">City</div><div class="val">{city}</div></div>
</div>

<div class="content">
  <h2>How We Sold It</h2>
  {data.get("full_description", "")}
  <br>
  {data.get("agent_bio", "")}
</div>

<footer class="footer">
  NAV Brokerage &nbsp;·&nbsp; {address} &nbsp;·&nbsp; {price}
</footer>

</body>
</html>"""


def _col_header(label: str, spec: str, color: str) -> None:
    st.markdown(
        f"<div style='border-top:3px solid {color};padding-top:.6rem;margin-bottom:.6rem'>"
        f"<span style='font-weight:700;font-size:.95rem;color:{color}'>{label}</span><br>"
        f"<span style='font-size:.75rem;color:#888'>{spec}</span></div>",
        unsafe_allow_html=True,
    )


def _render_image_card(key: str, b64: str | None, selected: bool) -> None:
    """16:9 aspect-ratio image card via pure HTML (no numpy / no st.image)."""
    border = f"3px solid {CRIMSON}" if selected else "3px solid transparent"
    filename = key.split("/")[-1]
    if b64:
        inner = (
            f"<img src='data:image/jpeg;base64,{b64}' "
            f"style='position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover'>"
        )
    else:
        inner = (
            "<div style='position:absolute;top:0;left:0;width:100%;height:100%;"
            "display:flex;align-items:center;justify-content:center;"
            "color:#ccc;font-size:1.2rem'>⚠</div>"
        )
    st.markdown(
        f"<div style='border:{border};border-radius:10px;overflow:hidden;"
        f"transition:border-color .15s;margin-bottom:0'>"
        f"<div style='position:relative;padding-bottom:56.25%;background:#e8e8e8'>"
        f"{inner}</div>"
        f"<div style='font-size:.66rem;color:#999;padding:3px 7px;"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
        f"background:#fafafa;border-top:1px solid #f0f0f0'>{filename}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Browse — Finder folder grid (pure native Streamlit)
# ---------------------------------------------------------------------------

def _render_folder_grid(
    prop_folders: list[str],
    all_objects: list[dict],
    master_objects: list[dict],
) -> None:
    """
    Mac Finder-style 4-column folder grid built entirely from native Streamlit:
    • st.button()   → open folder on click
    • st.checkbox() → styled as selection circle below each card
    • Selected ≥ 1  → Delete Selected Folders bar at top
    """

    # ── Read current selection from widget state ────────────────────────────
    selected_folders = [
        f for f in prop_folders
        if st.session_state.get(f"sel_folder_{_safe_key(f)}", False)
    ]

    # ── Action bar (visible only when folders are selected) ─────────────────
    if selected_folders:
        n_sel = len(selected_folders)
        st.markdown("<div class='action-bar'>", unsafe_allow_html=True)
        bar_a, bar_b, bar_c = st.columns([2, 4, 2])
        with bar_a:
            if st.button(
                f"🗑 Delete {n_sel} Folder{'s' if n_sel > 1 else ''}",
                key="bulk_del_folders",
                type="primary",
                use_container_width=True,
            ):
                st.session_state["confirm_bulk_delete"] = selected_folders[:]
        with bar_c:
            if st.button("✕ Clear selection", key="clear_folder_sel", use_container_width=True):
                for f in prop_folders:
                    k = f"sel_folder_{_safe_key(f)}"
                    if k in st.session_state:
                        st.session_state[k] = False
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Bulk-delete confirmation ─────────────────────────────────────────────
    if "confirm_bulk_delete" in st.session_state:
        targets = st.session_state["confirm_bulk_delete"]
        names = ", ".join(t.rstrip("/").split("/")[-1] for t in targets)
        st.warning(f"⚠ Permanently delete folders: **{names}**? All contents will be removed.")
        yes_c, no_c = st.columns(2)
        with yes_c:
            if st.button("✅ Confirm delete", key="bulk_del_confirm", type="primary"):
                for folder in targets:
                    if folder.startswith(f"{MASTER_FEATURED_PREFIX}/"):
                        continue
                    for obj in all_objects:
                        if obj["Key"].startswith(folder):
                            delete_object(obj["Key"])
                    st.session_state.pop(f"thumbs_{folder}", None)
                    st.session_state.pop(f"sel_folder_{_safe_key(folder)}", None)
                st.session_state.pop("confirm_bulk_delete", None)
                st.rerun()
        with no_c:
            if st.button("Cancel", key="bulk_del_cancel"):
                st.session_state.pop("confirm_bulk_delete", None)
                st.rerun()

    # ── Master Featured card (full-width, always first) ─────────────────────
    master_n = len(master_objects)
    master_size = _fmt_bytes(sum(o.get("Size", 0) for o in master_objects))
    st.markdown(
        "<div class='finder-card-master'>"
        "<div class='finder-card-icon'>⭐</div>",
        unsafe_allow_html=True,
    )
    if st.button("Master Featured", key="open_master", use_container_width=True):
        st.session_state["browse_open_folder"] = f"{MASTER_FEATURED_PREFIX}/"
        st.session_state.pop(f"thumbs_{MASTER_FEATURED_PREFIX}/", None)
        st.rerun()
    st.markdown(
        f"<div class='finder-card-meta'>{master_n} files · {master_size}</div>"
        f"</div>"
        f"<div style='text-align:center;font-size:.7rem;color:#bbb;"
        f"margin:.2rem 0 1.4rem'>🔒 Protected archive — never deleted</div>",
        unsafe_allow_html=True,
    )

    if not prop_folders:
        st.info("No property folders yet. Upload images to create one.")
        return

    st.markdown(
        "<div style='font-size:.72rem;font-weight:700;color:#aaa;"
        "text-transform:uppercase;letter-spacing:.07em;margin-bottom:.75rem'>"
        "Property Folders</div>",
        unsafe_allow_html=True,
    )

    # ── 4-column property folder grid ───────────────────────────────────────
    COLS = 4
    rows = [prop_folders[i:i + COLS] for i in range(0, len(prop_folders), COLS)]

    for row in rows:
        padded = row + [None] * (COLS - len(row))
        cols = st.columns(COLS)
        for col, folder in zip(cols, padded):
            if folder is None:
                continue
            safe_f = _safe_key(folder)
            folder_name = folder.rstrip("/").split("/")[-1]
            folder_objs = [o for o in all_objects if o["Key"].startswith(folder)]
            n_items = len(folder_objs)
            folder_size = _fmt_bytes(sum(o.get("Size", 0) for o in folder_objs))

            with col:
                # Card shell: emoji + transparent name button + meta line
                st.markdown(
                    "<div class='finder-card'>"
                    "<div class='finder-card-icon'>📁</div>",
                    unsafe_allow_html=True,
                )
                if st.button(folder_name, key=f"fc_{safe_f}", use_container_width=True):
                    st.session_state["browse_open_folder"] = folder
                    st.session_state.pop(f"thumbs_{folder}", None)
                    st.rerun()
                st.markdown(
                    f"<div class='finder-card-meta'>{n_items} files · {folder_size}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # Selection circle checkbox
                st.markdown("<div class='sel-circle'>", unsafe_allow_html=True)
                st.checkbox(
                    "Select",
                    key=f"sel_folder_{safe_f}",
                    label_visibility="collapsed",
                )
                st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Browse — Opened folder view (pure native Streamlit)
# ---------------------------------------------------------------------------

def _render_folder_contents(folder: str, all_objects: list[dict]) -> None:
    """
    Opened folder: 16:9 image grid with:
    • Checkbox-based image selection (circle-styled)
    • Delete Selected + Clear Folder pinned to top header
    • Sort controls + Save Current Order → sort_order.json
    • Silent spinner thumbnail loading (session-cached)
    """
    is_master = folder.startswith(f"{MASTER_FEATURED_PREFIX}/")
    folder_label = folder.rstrip("/").split("/")[-1]
    icon = "⭐" if is_master else "📁"

    objects = [o for o in all_objects if o["Key"].startswith(folder)]
    meta_by_key = {o["Key"]: o for o in objects}

    image_keys: list[str] = [
        o["Key"] for o in objects
        if _is_image(o["Key"])
    ]
    other_keys: list[str] = [
        o["Key"] for o in objects
        if not _is_image(o["Key"]) and not o["Key"].endswith("sort_order.json")
    ]
    total_size = sum(o.get("Size", 0) for o in objects)

    # ── Apply saved sort order ──────────────────────────────────────────────
    saved_order = _load_sort_order(folder)
    if saved_order:
        existing_set = set(image_keys)
        ordered = [k for k in saved_order if k in existing_set]
        new_keys = [k for k in image_keys if k not in set(ordered)]
        image_keys = ordered + new_keys

    # ── Apply sort radio ────────────────────────────────────────────────────
    sort_mode = st.session_state.get("browse_sort", "Custom order")
    if sort_mode == "Filename A→Z":
        image_keys = sorted(image_keys)
    elif sort_mode == "Filename Z→A":
        image_keys = sorted(image_keys, reverse=True)
    elif sort_mode == "Date ↓":
        image_keys = sorted(image_keys,
                            key=lambda k: str(meta_by_key[k].get("LastModified", "")),
                            reverse=True)
    elif sort_mode == "Date ↑":
        image_keys = sorted(image_keys,
                            key=lambda k: str(meta_by_key[k].get("LastModified", "")))

    if st.session_state.get("browse_reversed", False):
        image_keys.reverse()

    # ── Read image selections from widget state (previous rerun) ───────────
    selected_imgs: list[str] = [
        k for k in image_keys
        if st.session_state.get(f"sel_img_{_safe_key(k)}", False)
    ]
    n_img_sel = len(selected_imgs)

    # ── TOP HEADER ROW ─────────────────────────────────────────────────────
    back_c, title_c, del_c, wipe_c = st.columns([1, 5, 2, 2])

    with back_c:
        if st.button("⬅ Back", key="back_btn", use_container_width=True):
            st.session_state.pop("browse_open_folder", None)
            st.session_state.pop(f"thumbs_{folder}", None)
            st.session_state.pop("browse_reversed", None)
            st.rerun()

    with title_c:
        st.markdown(
            f"<h3 style='margin:0;padding-top:.2rem'>{icon} {folder_label}</h3>"
            f"<span style='font-size:.76rem;color:#aaa'>"
            f"{len(image_keys)} images · {_fmt_bytes(total_size)}</span>",
            unsafe_allow_html=True,
        )

    with del_c:
        if n_img_sel:
            if st.button(
                f"🗑 Delete {n_img_sel} Photo{'s' if n_img_sel > 1 else ''}",
                key="del_sel_imgs",
                type="primary",
                use_container_width=True,
            ):
                for k in selected_imgs:
                    delete_object(k)
                    st.session_state.pop(f"sel_img_{_safe_key(k)}", None)
                st.session_state.pop(f"thumbs_{folder}", None)
                st.rerun()

    with wipe_c:
        if not is_master and image_keys:
            if st.button("🗑️ Clear Folder", key="clear_folder_btn", use_container_width=True):
                st.session_state["confirm_wipe"] = folder

    st.markdown("<div style='margin:.3rem 0'></div>", unsafe_allow_html=True)

    # ── Clear-folder confirmation ───────────────────────────────────────────
    if st.session_state.get("confirm_wipe") == folder:
        st.warning(
            f"⚠ Permanently wipe all **{len(image_keys)} image(s)** from `{folder_label}/`? "
            f"Featured banners are safe in `{MASTER_FEATURED_PREFIX}/`."
        )
        yw, nw = st.columns(2)
        with yw:
            if st.button("✅ Yes, wipe folder", key="wipe_yes", type="primary"):
                for o in objects:
                    delete_object(o["Key"])
                st.session_state.pop("confirm_wipe", None)
                st.session_state.pop("browse_open_folder", None)
                st.session_state.pop(f"thumbs_{folder}", None)
                st.success(f"`{folder_label}/` cleared.")
                st.rerun()
        with nw:
            if st.button("Cancel", key="wipe_no"):
                st.session_state.pop("confirm_wipe", None)
                st.rerun()

    if not image_keys:
        st.info("No images in this folder.")
        if other_keys:
            with st.expander(f"Other files ({len(other_keys)})"):
                for k in other_keys:
                    st.write(f"`{k.split('/')[-1]}`")
        return

    # ── Sort controls + Save Order ──────────────────────────────────────────
    sort_a, sort_b = st.columns([6, 2])
    with sort_a:
        new_sort = st.radio(
            "Sort", ["Custom order", "Filename A→Z", "Filename Z→A", "Date ↓", "Date ↑"],
            horizontal=True,
            key="browse_sort",
            label_visibility="collapsed",
        )
    with sort_b:
        if st.button(
            "📌 Save Order",
            key="save_order_btn",
            use_container_width=True,
            help="Reads the position numbers below each photo and saves that sequence as the default order for this folder",
        ):
            # Read each image's position number_input, sort by it, save to R2
            order_pairs = [
                (int(st.session_state.get(f"order_{_safe_key(k)}", i + 1)), i, k)
                for i, k in enumerate(image_keys)
            ]
            order_pairs.sort(key=lambda x: (x[0], x[1]))
            sorted_keys = [k for _, _, k in order_pairs]
            _save_sort_order(folder, sorted_keys)
            # Clear position inputs so they reset to the new sequence
            for k in image_keys:
                st.session_state.pop(f"order_{_safe_key(k)}", None)
            st.session_state.pop(f"thumbs_{folder}", None)
            st.success("Order saved.", icon="📌")
            st.rerun()

    # Re-apply sort if radio just changed
    if new_sort != sort_mode:
        st.rerun()

    # ── Load thumbnails — silent, session-cached ────────────────────────────
    cache_key = f"thumbs_{folder}"
    if cache_key not in st.session_state:
        with st.spinner("Preparing images…"):
            thumbs: dict[str, str | None] = {k: _thumbnail_b64(k) for k in image_keys}
        st.session_state[cache_key] = thumbs
    else:
        thumbs = st.session_state[cache_key]
        missing = [k for k in image_keys if k not in thumbs]
        if missing:
            with st.spinner(f"Loading {len(missing)} new image{'s' if len(missing) > 1 else ''}…"):
                for k in missing:
                    thumbs[k] = _thumbnail_b64(k)
            st.session_state[cache_key] = thumbs

    # ── 3-column image grid ─────────────────────────────────────────────────
    st.markdown("<div style='margin-top:.5rem'></div>", unsafe_allow_html=True)
    img_cols = st.columns(3)
    for idx, key in enumerate(image_keys):
        safe_k = _safe_key(key)
        is_sel = st.session_state.get(f"sel_img_{safe_k}", False)
        with img_cols[idx % 3]:
            # 16:9 image with crimson border when selected
            _render_image_card(key, thumbs.get(key), is_sel)

            # ── Click-to-select toggle button ────────────────────────────
            # Full-width button fused to the bottom of the image card.
            # Clicking anywhere on it toggles selection (crimson = selected).
            st.markdown("<div class='img-sel-btn'>", unsafe_allow_html=True)
            btn_label = "✓  Selected" if is_sel else "○  Select"
            btn_type  = "primary"    if is_sel else "secondary"
            if st.button(
                btn_label,
                key=f"selb_img_{safe_k}",
                type=btn_type,
                use_container_width=True,
            ):
                st.session_state[f"sel_img_{safe_k}"] = not is_sel
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

            # ── Position number input for manual reordering ──────────────
            st.markdown("<div class='img-order-input'>", unsafe_allow_html=True)
            st.number_input(
                "Position",
                min_value=1,
                max_value=len(image_keys),
                value=idx + 1,
                step=1,
                key=f"order_{safe_k}",
                label_visibility="collapsed",
                help=f"Set display position for this photo, then click 📌 Save Order",
            )
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Other (non-image) files ─────────────────────────────────────────────
    if other_keys:
        with st.expander(f"Other files ({len(other_keys)})"):
            for key in other_keys:
                m = meta_by_key.get(key, {})
                st.write(f"`{key.split('/')[-1]}` — {_fmt_bytes(m.get('Size', 0))}")
                st.markdown(f"[Presigned link (1h)]({presigned_url(key)})")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_browse() -> None:
    st.header("Browse bucket")

    with st.spinner("Loading bucket…"):
        try:
            all_objects = list_objects(prefix="")
        except (BotoCoreError, ClientError) as exc:
            st.error(f"Could not list objects: {exc}")
            return

    if not all_objects:
        st.info("Bucket is empty.")
        return

    master_objects = [o for o in all_objects if o["Key"].startswith(f"{MASTER_FEATURED_PREFIX}/")]
    prop_objects   = [o for o in all_objects if not o["Key"].startswith(f"{MASTER_FEATURED_PREFIX}/")]

    seen: set[str] = set()
    prop_folders: list[str] = []
    for o in prop_objects:
        parts = o["Key"].split("/")
        if len(parts) >= 2 and parts[0] == "properties" and parts[1]:
            f = f"properties/{parts[1]}/"
            if f not in seen:
                seen.add(f)
                prop_folders.append(f)
    prop_folders.sort()

    open_folder = st.session_state.get("browse_open_folder")
    if open_folder:
        _render_folder_contents(open_folder, all_objects)
    else:
        _render_folder_grid(prop_folders, prop_objects, master_objects)


def page_upload() -> None:
    st.header("Upload Images")

    prop_input = st.text_input(
        "🏠 Property Name or ID",
        placeholder="e.g. 369 Kendrick Ln",
        help="Sets the folder and filename prefix for every upload on this page.",
    )
    st.markdown("**Enter a Property Name or ID above before staging files or uploading.**")
    prefix = prop_input.strip().replace(" ", "-").lower()

    if prefix:
        st.caption(
            f"Gallery path: **`properties/{prefix}/`** · "
            f"Banner path: **`{MASTER_FEATURED_PREFIX}/`**"
        )
    else:
        st.info("⬆ Enter a Property Name or ID above, then stage files in any column.")

    st.markdown("<div style='margin:.75rem 0 .25rem'></div>", unsafe_allow_html=True)

    col_gallery, col_banner, col_story = st.columns(3, gap="medium")

    with col_gallery:
        _col_header(
            "Portfolio Gallery",
            f"Max {GALLERY_MAX_HEIGHT}px height · WebP · continues existing sequence",
            SLATE,
        )
        gallery_files = st.file_uploader(
            "Images (multiple allowed)", type=GALLERY_TYPES,
            accept_multiple_files=True, key="files_gallery", label_visibility="collapsed",
        )
        gallery_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_gallery")
        if gallery_files:
            st.caption(f"{len(gallery_files)} file(s) staged — sequence auto-continues")

    with col_banner:
        _col_header(
            "Featured Banner",
            f"Exactly {BANNER_WIDTH}px wide · Archived to Master Featured folder",
            CRIMSON,
        )
        banner_file = st.file_uploader(
            "One image", type=GALLERY_TYPES, accept_multiple_files=False,
            key="files_banner", label_visibility="collapsed",
        )
        banner_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_banner")
        if banner_file:
            st.caption(
                f"Staged → `{MASTER_FEATURED_PREFIX}/{prefix or '<prefix>'}-featured.webp` ⭐"
            )

    with col_story:
        _col_header(
            "Story Cover",
            f"Max {STORY_COVER_MAX_WIDTH}px wide · GIF preserved · WebP otherwise",
            BLACK,
        )
        story_file = st.file_uploader(
            "One GIF or image", type=SUPPORTED_UPLOAD_TYPES, accept_multiple_files=False,
            key="files_story", label_visibility="collapsed",
        )
        story_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_story")
        if story_file:
            src_ext_p = story_file.name.rsplit(".", 1)[-1].lower()
            st.caption(
                f"Staged → `{prefix or '<prefix>'}-story-cover."
                f"{'gif' if src_ext_p == 'gif' else 'webp'}`"
            )

    st.markdown("<div style='margin:1.25rem 0 .5rem'></div>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        master_clicked = st.button(
            "🚀  Process & Upload Property Media Package",
            type="primary", use_container_width=True, disabled=not prefix,
        )

    if not master_clicked:
        _render_upload_sequence(prefix)
        return

    if not prefix:
        st.error("Enter a Property Name or ID first.")
        return

    if not (gallery_files or banner_file or story_file):
        st.warning("Stage at least one file in a column before uploading.")
        return

    st.markdown("---")
    total_ok = 0
    total_err = 0

    if gallery_files:
        st.markdown(
            f"<span style='font-weight:600;color:{SLATE}'>Portfolio Gallery</span>",
            unsafe_allow_html=True,
        )
        with st.spinner("Checking existing sequence…"):
            start_seq = _next_gallery_seq(prefix)
        prog = st.progress(0, text="Processing gallery…")
        newly_uploaded: list[tuple[str, str | None]] = []
        for idx, f in enumerate(gallery_files):
            prog.progress(idx / len(gallery_files), text=f"{f.name}…")
            raw = f.read()
            try:
                data, _ = process_gallery(raw, quality=gallery_quality)
                seq = str(start_seq + idx).zfill(2)
                r2_key = f"properties/{prefix}/{prefix}-{seq}.webp"
                upload_object(r2_key, data, "image/webp")
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(f"✓ `{prefix}-{seq}.webp` — {_fmt_bytes(len(data))} ({savings:+.0f}%)")
                total_ok += 1
                try:
                    ti = Image.open(io.BytesIO(data)).convert("RGB")
                    tw, th = ti.size
                    if tw > 320:
                        ti = ti.resize((320, int(th * 320 / tw)), Image.LANCZOS)
                    tbuf = io.BytesIO()
                    ti.save(tbuf, format="JPEG", quality=65)
                    t_b64: str | None = base64.b64encode(tbuf.getvalue()).decode()
                except Exception:
                    t_b64 = None
                newly_uploaded.append((r2_key, t_b64))
            except Exception as exc:
                st.error(f"✗ {f.name}: {exc}")
                total_err += 1
        prog.progress(1.0, text="Gallery done")
        if start_seq > 1:
            st.caption(
                f"ℹ Continued from existing sequence — "
                f"started at `{prefix}-{str(start_seq).zfill(2)}.webp`"
            )
        if newly_uploaded:
            st.session_state["last_gallery_keys"] = [k for k, _ in newly_uploaded]
            st.session_state["last_gallery_thumbs"] = {k: t for k, t in newly_uploaded}
            st.session_state["last_gallery_prefix"] = prefix

    if banner_file:
        st.markdown(
            f"<span style='font-weight:600;color:{CRIMSON}'>Featured Banner → Master Archive</span>",
            unsafe_allow_html=True,
        )
        with st.spinner(f"Processing {banner_file.name}…"):
            try:
                raw = banner_file.read()
                data, _ = process_banner(raw, quality=banner_quality)
                r2_key = f"{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp"
                upload_object(r2_key, data, "image/webp")
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(
                    f"✓ `{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp` — "
                    f"{_fmt_bytes(len(data))} ({savings:+.0f}%) ⭐"
                )
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {banner_file.name}: {exc}")
                total_err += 1

    if story_file:
        st.markdown(
            f"<span style='font-weight:600;color:{BLACK}'>Story Cover</span>",
            unsafe_allow_html=True,
        )
        with st.spinner(f"Processing {story_file.name}…"):
            try:
                raw = story_file.read()
                src_ext = story_file.name.rsplit(".", 1)[-1].lower()
                data, out_ext = process_story_cover(raw, quality=story_quality, src_ext=src_ext)
                ct = "image/gif" if out_ext == "gif" else "image/webp"
                r2_key = f"properties/{prefix}/{prefix}-story-cover.{out_ext}"
                upload_object(r2_key, data, ct)
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(
                    f"✓ `{prefix}-story-cover.{out_ext}` — "
                    f"{_fmt_bytes(len(data))} ({savings:+.0f}%)"
                )
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {story_file.name}: {exc}")
                total_err += 1

    st.markdown("---")
    if total_err == 0:
        st.success(f"🎉 Package complete — {total_ok} file(s) uploaded.")
    else:
        st.warning(f"{total_ok} uploaded · {total_err} failed — check errors above.")

    _render_upload_sequence(prefix)


def page_compile() -> None:
    st.header("Compile Listing")

    # ── Property & inputs ────────────────────────────────────────────────────
    default_prefix = st.session_state.get("last_gallery_prefix", "")
    prop_input = st.text_input(
        "🏠 Property Name or ID",
        value=default_prefix,
        placeholder="e.g. 369-kendrick-ln",
        help="Must match the Upload Images prefix so gallery URLs resolve correctly.",
    )
    prefix = prop_input.strip().replace(" ", "-").lower()

    st.markdown("<div style='margin:.5rem 0'></div>", unsafe_allow_html=True)

    left_col, right_col = st.columns([3, 1])
    with left_col:
        mls_text = st.text_area(
            "📋 Paste Zillow / MLS Raw Text Dump Here",
            height=220,
            placeholder=(
                "Paste the full listing copy from Zillow, MLS, or any text source here.\n\n"
                "Include: address, price, beds, baths, sq ft, year built, description, features, agent info…"
            ),
        )
        studeo_url = st.text_input(
            "🔗 Studeo.ai Interactive Booklet URL",
            placeholder="https://studeo.ai/listing/...",
        )
    with right_col:
        st.markdown("<div style='margin-top:1.6rem'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:.78rem;font-weight:700;color:#aaa;"
            "text-transform:uppercase;letter-spacing:.06em;margin-bottom:.5rem'>"
            "Output Mode</div>",
            unsafe_allow_html=True,
        )
        mode = st.radio(
            "Output Mode",
            ["For Sale", "Sold"],
            key="compile_mode",
            label_visibility="collapsed",
        )
        st.markdown(
            "<div style='font-size:.72rem;color:#aaa;margin-top:.35rem'>"
            "<b>For Sale</b> — generates full HTML listing.<br><br>"
            "<b>Sold</b> — wipes gallery folder from R2 (preserves Master Featured) "
            "and outputs archive template.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin:.5rem 0'></div>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        compile_clicked = st.button(
            "✨  Compile Listing",
            type="primary",
            use_container_width=True,
            disabled=not (prefix and mls_text.strip()),
        )

    if not compile_clicked:
        return

    # ── AI compile ──────────────────────────────────────────────────────────
    try:
        with st.spinner("GPT-5 parsing listing and generating content…"):
            result = _compile_listing_ai(prefix, mls_text, studeo_url)
        st.session_state["compile_result"] = result
        st.session_state["compile_prefix"] = prefix
        st.session_state["compile_studeo"] = studeo_url
        st.session_state["compile_mode"] = mode
    except Exception as exc:
        st.error(f"AI compile failed: {exc}")
        return

    # ── Results section ──────────────────────────────────────────────────────
    _render_compile_results(prefix, result, studeo_url, mode)


def _render_compile_results(prefix: str, result: dict, studeo_url: str, mode: str) -> None:
    stats = result.get("stats", {})

    # Stats summary row
    st.markdown("<div style='margin:1rem 0 .3rem'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:.78rem;font-weight:700;color:#aaa;"
        "text-transform:uppercase;letter-spacing:.06em'>Extracted Stats</div>",
        unsafe_allow_html=True,
    )
    s_cols = st.columns(7)
    for col, (lbl, val) in zip(s_cols, [
        ("Address",  stats.get("address", "—")),
        ("Price",    stats.get("price", "—")),
        ("Beds",     stats.get("beds", "—")),
        ("Baths",    stats.get("baths", "—")),
        ("Sq Ft",    stats.get("sqft", "—")),
        ("Year",     stats.get("year", "—")),
        ("MLS#",     stats.get("mls", "—")),
    ]):
        with col:
            st.markdown(
                f"<div style='border:1.5px solid #eaeaea;border-radius:10px;"
                f"padding:.6rem .7rem;text-align:center'>"
                f"<div style='font-size:.62rem;color:#aaa;text-transform:uppercase;"
                f"letter-spacing:.05em'>{lbl}</div>"
                f"<div style='font-size:.85rem;font-weight:700;color:#1a1a1a;"
                f"margin-top:.2rem;word-break:break-word'>{val}</div></div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='margin:.75rem 0'></div>", unsafe_allow_html=True)

    # Marketing copy segments
    with st.expander("📝 Full Description", expanded=False):
        st.markdown(result.get("full_description", ""), unsafe_allow_html=True)
    with st.expander("🏘 Neighborhood", expanded=False):
        st.markdown(result.get("neighborhood", ""), unsafe_allow_html=True)
    with st.expander("🌅 Lifestyle", expanded=False):
        st.markdown(result.get("lifestyle", ""), unsafe_allow_html=True)
    with st.expander("🤝 Agent Bio", expanded=False):
        st.markdown(result.get("agent_bio", ""), unsafe_allow_html=True)

    st.markdown("<div style='margin:.5rem 0'></div>", unsafe_allow_html=True)

    # ── Flyer bullets ────────────────────────────────────────────────────────
    st.markdown(
        "<div style='font-size:.78rem;font-weight:700;color:#aaa;"
        "text-transform:uppercase;letter-spacing:.06em;margin-bottom:.35rem'>"
        "Flyer Bullets (6)</div>",
        unsafe_allow_html=True,
    )
    bullets_txt = "\n".join(
        f"• {b}" for b in result.get("flyer_bullets", [])
    )
    st.text_area("Flyer Bullets", value=bullets_txt, height=160, label_visibility="collapsed")

    # ── Social post ─────────────────────────────────────────────────────────
    st.markdown(
        "<div style='font-size:.78rem;font-weight:700;color:#aaa;"
        "text-transform:uppercase;letter-spacing:.06em;margin:.75rem 0 .35rem'>"
        "Social Media Post</div>",
        unsafe_allow_html=True,
    )
    st.text_area(
        "Social Post", value=result.get("social_post", ""),
        height=200, label_visibility="collapsed",
    )

    st.markdown("---")

    # ── HTML template output ─────────────────────────────────────────────────
    if mode == "For Sale":
        st.markdown(
            "<div style='font-size:.78rem;font-weight:700;color:#aaa;"
            "text-transform:uppercase;letter-spacing:.06em;margin-bottom:.5rem'>"
            "For Sale — Responsive HTML Listing Template</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Gallery & banner images use 7-day presigned R2 URLs. "
            "Copy the HTML block below and paste into your website CMS."
        )
        try:
            html_out = _for_sale_html(prefix, result, studeo_url)
            st.text_area(
                "HTML", value=html_out, height=420, label_visibility="collapsed",
            )
            dl_b64 = base64.b64encode(html_out.encode()).decode()
            dl_href = (
                f"<a href='data:text/html;base64,{dl_b64}' "
                f"download='{prefix}-listing.html' "
                f"style='display:inline-block;margin-top:.5rem;padding:.45rem 1.1rem;"
                f"background:#990000;color:#fff;border-radius:6px;font-size:.82rem;"
                f"font-weight:700;text-decoration:none'>⬇ Download HTML</a>"
            )
            st.markdown(dl_href, unsafe_allow_html=True)
        except Exception as exc:
            st.error(f"HTML generation failed: {exc}")

    else:  # Sold mode
        st.markdown(
            "<div style='font-size:.78rem;font-weight:700;color:#990000;"
            "text-transform:uppercase;letter-spacing:.06em;margin-bottom:.5rem'>"
            "⚠ Sold Mode — Wipe Gallery Folder</div>",
            unsafe_allow_html=True,
        )
        folder = f"properties/{prefix}/"
        st.warning(
            f"This will permanently delete all images in `{folder}` from R2. "
            f"The Master Featured banner is preserved. This cannot be undone."
        )
        confirm_col, _ = st.columns([1, 3])
        with confirm_col:
            if st.button("✅ Confirm — Wipe & Generate Archive", type="primary", key="sold_confirm"):
                with st.spinner("Wiping gallery folder…"):
                    try:
                        objs = list_objects(prefix=folder)
                        for o in objs:
                            delete_object(o["Key"])
                        st.session_state.pop("last_gallery_keys", None)
                        st.session_state.pop("last_gallery_thumbs", None)
                        st.success(f"✓ `{folder}` wiped ({len(objs)} objects removed).")
                    except Exception as exc:
                        st.error(f"Wipe failed: {exc}")
                        return

                st.markdown(
                    "<div style='font-size:.78rem;font-weight:700;color:#aaa;"
                    "text-transform:uppercase;letter-spacing:.06em;margin:.75rem 0 .35rem'>"
                    "How We Sold It — Archive HTML</div>",
                    unsafe_allow_html=True,
                )
                try:
                    html_out = _sold_html(prefix, result)
                    st.text_area(
                        "Sold HTML", value=html_out, height=380,
                        label_visibility="collapsed",
                    )
                    dl_b64 = base64.b64encode(html_out.encode()).decode()
                    dl_href = (
                        f"<a href='data:text/html;base64,{dl_b64}' "
                        f"download='{prefix}-sold.html' "
                        f"style='display:inline-block;margin-top:.5rem;padding:.45rem 1.1rem;"
                        f"background:#990000;color:#fff;border-radius:6px;font-size:.82rem;"
                        f"font-weight:700;text-decoration:none'>⬇ Download HTML</a>"
                    )
                    st.markdown(dl_href, unsafe_allow_html=True)
                except Exception as exc:
                    st.error(f"Sold template generation failed: {exc}")


def page_settings() -> None:
    st.header("Connection settings")
    st.info(
        "Credentials are loaded from Streamlit secrets (Streamlit Cloud) "
        "or environment variables (Replit/local)."
    )
    rows_md = ["| Variable | Status |", "|---|:---:|"]
    for var in ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]:
        rows_md.append(f"| `{var}` | {'✅ Set' if _get_secret(var) else '❌ Missing'} |")
    st.markdown("\n".join(rows_md))

    st.markdown("---")
    st.subheader("Bucket health check")
    if st.button("Test connection"):
        with st.spinner("Connecting…"):
            try:
                get_r2_client().head_bucket(Bucket=BUCKET_NAME)
                st.success(f"Connected to **{BUCKET_NAME}** successfully.")
            except ClientError as exc:
                st.error(f"Connection failed ({exc.response['Error']['Code']}): {exc}")
            except EnvironmentError as exc:
                st.error(str(exc))

    st.markdown("---")
    st.subheader("R2 CORS configuration")
    st.markdown(
        "Add this JSON block to your Cloudflare R2 bucket **CORS policy** "
        "(R2 → Bucket → Settings → CORS):"
    )
    st.code(json.dumps(R2_CORS_CONFIG, indent=2), language="json")


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🏠", layout="wide")
    _inject_css()

    if os.path.isfile(_IVAN_IMG_PATH):
        import base64
        with open(_IVAN_IMG_PATH, "rb") as _f:
            _b64 = base64.b64encode(_f.read()).decode()
        st.sidebar.markdown(
            f"<img src='data:image/png;base64,{_b64}' style='width:100%;border-radius:8px;'>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            "<div style='text-align:center;font-size:3rem'>🧭</div>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown(
        f"<h2 style='color:{CRIMSON};margin-top:.25rem;text-align:center;"
        f"font-size:1.15rem'>{APP_NAME}</h2>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<hr class='sidebar-divider'>", unsafe_allow_html=True)

    page = st.sidebar.radio(
        "Navigate", ["Browse bucket", "Upload Images", "Compile Listing", "Settings"],
        label_visibility="collapsed",
    )

    try:
        _load_r2_config()
    except EnvironmentError as exc:
        st.error(str(exc))
        st.stop()

    if page == "Browse bucket":
        page_browse()
    elif page == "Upload Images":
        page_upload()
    elif page == "Compile Listing":
        page_compile()
    elif page == "Settings":
        page_settings()


if __name__ == "__main__":
    main()
