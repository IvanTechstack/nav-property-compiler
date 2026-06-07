"""Ivan's Image Optimizer — Property media management via Cloudflare R2."""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass

import boto3
import streamlit as st
import streamlit.components.v1 as components
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
    """Load sort_order.json from R2. Returns ordered key list or None."""
    try:
        data = download_object(f"{folder}sort_order.json")
        return json.loads(data).get("order")
    except Exception:
        return None


def _save_sort_order(folder: str, ordered_keys: list[str]) -> None:
    """Persist sort_order.json to R2 for a folder."""
    upload_object(
        f"{folder}sort_order.json",
        json.dumps({"order": ordered_keys}).encode(),
        "application/json",
    )


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _is_image(key: str) -> bool:
    return key.lower().rsplit(".", 1)[-1] in SUPPORTED_UPLOAD_TYPES


def _is_master_featured(key: str) -> bool:
    return key.startswith(f"{MASTER_FEATURED_PREFIX}/")


def _thumbnail_b64(key: str, max_w: int = 360) -> str | None:
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
        img.save(buf, format="JPEG", quality=70, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _inject_css() -> None:
    st.markdown(f"""
<style>
/* ── Global buttons ─────────────────────────────────────────── */
div.stButton > button[kind="primary"],
div.stFormSubmitButton > button[kind="primary"] {{
    background-color: {CRIMSON} !important;
    border-color: {CRIMSON} !important;
    color: #fff !important;
}}
div.stButton > button[kind="primary"]:hover,
div.stFormSubmitButton > button[kind="primary"]:hover {{
    background-color: #7a0000 !important;
    border-color: #7a0000 !important;
}}

/* ── Sidebar ─────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {{
    background-color: #ffffff !important;
    border-right: 3px solid {CRIMSON};
}}
section[data-testid="stSidebar"] > div {{
    background-color: #ffffff !important;
}}
.sidebar-divider {{
    border: none; border-top: 1px solid #d9d9d9; margin: 0.75rem 0 1rem 0;
}}

/* ── Upload column headers ───────────────────────────────────── */
.upload-col {{ border: 1px solid {SLATE}; border-radius: 8px; padding: 1rem; }}

/* ─────────────────────────────────────────────────────────────
   FEATURE 1 — Interactive folder card buttons
   Single full-surface Streamlit button styled as a card.
   ─────────────────────────────────────────────────────────── */
.folder-card-btn [data-testid="stButton"] button {{
    background: #fafafa !important;
    border: 1px solid #e8e8e8 !important;
    border-radius: 14px !important;
    height: 128px !important;
    width: 100% !important;
    white-space: pre-line !important;
    font-size: 0.82rem !important;
    line-height: 1.65 !important;
    color: #222 !important;
    padding: 0.9rem 0.6rem !important;
    transition: transform 0.18s ease, box-shadow 0.18s ease,
                border-color 0.18s ease, background 0.18s ease !important;
    cursor: pointer !important;
}}
.folder-card-btn [data-testid="stButton"] button:hover {{
    transform: translateY(-5px) !important;
    box-shadow: 0 10px 32px rgba(0,0,0,0.13) !important;
    border-color: {SLATE} !important;
    background: #ffffff !important;
}}
/* Master card gets crimson accent on hover */
.folder-card-btn.master-card [data-testid="stButton"] button {{
    background: #fff8f8 !important;
    border-color: {CRIMSON} !important;
}}
.folder-card-btn.master-card [data-testid="stButton"] button:hover {{
    box-shadow: 0 10px 32px rgba(153,0,0,0.15) !important;
    border-color: #7a0000 !important;
    background: #fff4f4 !important;
}}
/* Delete-folder buttons: flat, small */
.folder-del-btn [data-testid="stButton"] button {{
    font-size: 0.74rem !important;
    color: #bbb !important;
    background: transparent !important;
    border: none !important;
    padding: 0.15rem 0 !important;
}}
.folder-del-btn [data-testid="stButton"] button:hover {{
    color: {CRIMSON} !important;
    background: transparent !important;
}}

/* ── Section label ───────────────────────────────────────────── */
.section-label {{
    font-size: 0.73rem; font-weight: 700; color: {SLATE};
    text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 0.6rem;
}}
</style>
""", unsafe_allow_html=True)


def _col_header(label: str, spec: str, color: str) -> None:
    st.markdown(
        f"<div style='border-top:3px solid {color};padding-top:0.6rem;margin-bottom:0.6rem'>"
        f"<span style='font-weight:700;font-size:0.95rem;color:{color}'>{label}</span><br>"
        f"<span style='font-size:0.75rem;color:#888'>{spec}</span></div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Interactive image grid (features 2 + 3)
# ---------------------------------------------------------------------------

def _build_image_grid_html(
    image_keys: list[str],
    thumbs: dict[str, str | None],
    folder: str,
    has_custom_order: bool,
) -> tuple[str, int]:
    """
    Build a self-contained HTML panel with:
      • Click-to-highlight selection (crimson border, no checkboxes)
      • Sortable.js drag-and-drop reordering
      • 'Delete Selected' + 'Apply Sort Order' action buttons
      • Both actions redirect window.top with ?action=…&folder=…&keys=…
    Returns (html_string, iframe_height_px).
    """
    cards_html = ""
    for key in image_keys:
        b64 = thumbs.get(key)
        filename = key.split("/")[-1]
        key_json = json.dumps(key)  # safely escaped for JS/HTML
        if b64:
            img_markup = (
                f"<img src='data:image/jpeg;base64,{b64}' "
                f"style='width:100%;height:145px;object-fit:cover;display:block;pointer-events:none'>"
            )
        else:
            img_markup = (
                "<div style='height:145px;background:#f0f0f0;display:flex;"
                "align-items:center;justify-content:center;color:#ccc;font-size:0.8rem'>⚠</div>"
            )
        cards_html += f"""
<div class="card" data-key={key_json}>
  <div class="drag-handle">⠿ ⠿</div>
  {img_markup}
  <div class="fname">{filename}</div>
</div>"""

    folder_json = json.dumps(folder)
    n = len(image_keys)
    rows = max(1, (n + 2) // 3)
    # 165px per row (145 image + 20 fname/handle) + 10px gap + 68px action bar + 24px padding
    height = rows * 175 + 92

    custom_note = (
        "<span style='font-size:0.7rem;color:#708090;margin-left:auto'>"
        "📌 Custom order active</span>"
        if has_custom_order else ""
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;
   font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
body{{padding:10px 10px 6px;background:transparent}}
#grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.card{{
  border-radius:10px;overflow:hidden;cursor:pointer;
  border:3px solid transparent;
  transition:border-color .14s,box-shadow .14s,transform .12s;
  background:#f9f9f9;user-select:none
}}
.card:hover{{transform:scale(1.015)}}
.card.sel{{
  border-color:#990000;
  box-shadow:0 0 0 1px rgba(153,0,0,.22)
}}
.drag-handle{{
  text-align:center;padding:3px 0;color:#ccc;
  font-size:.72rem;letter-spacing:3px;cursor:grab;
  background:#f4f4f4;border-bottom:1px solid #eee
}}
.drag-handle:active{{cursor:grabbing}}
.fname{{
  font-size:.66rem;padding:3px 6px;color:#888;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis
}}
.sortable-ghost{{opacity:.3}}
.sortable-chosen{{transform:scale(1.04);
  box-shadow:0 10px 28px rgba(0,0,0,.2)!important;z-index:9}}
.bar{{
  display:flex;align-items:center;gap:8px;
  margin-top:10px;padding:7px 10px;
  background:#f5f5f5;border-radius:8px
}}
.btn{{border:none;border-radius:7px;padding:6px 13px;
  font-size:.78rem;font-weight:600;cursor:pointer;
  transition:opacity .15s}}
.btn:hover{{opacity:.82}}
.btn-del{{background:#990000;color:#fff}}
.btn-del:disabled{{background:#e0e0e0;color:#aaa;cursor:default;opacity:1}}
.btn-sort{{background:#708090;color:#fff}}
.hint{{font-size:.72rem;color:#aaa;flex:1}}
</style>
</head>
<body>
<div id="grid">
{cards_html}
</div>
<div class="bar">
  <button class="btn btn-del" id="bdel" disabled onclick="doDel()">Delete Selected</button>
  <span class="hint" id="hint">Click to select · Drag handle ⠿ to reorder</span>
  {custom_note}
  <button class="btn btn-sort" onclick="doSort()">💾 Apply Sort Order</button>
</div>
<script>
const grid=document.getElementById('grid');
const bdel=document.getElementById('bdel');
const hint=document.getElementById('hint');
const folder={folder_json};
const sel=new Set();

// ── Click-to-highlight selection ──────────────────────────────
grid.querySelectorAll('.card').forEach(function(c){{
  c.addEventListener('click',function(e){{
    if(e.target.classList.contains('drag-handle'))return;
    const k=c.dataset.key;
    if(sel.has(k)){{sel.delete(k);c.classList.remove('sel')}}
    else{{sel.add(k);c.classList.add('sel')}}
    updateBar();
  }});
}});

function updateBar(){{
  const n=sel.size;
  bdel.disabled=n===0;
  bdel.textContent=n>0?'🗑 Delete '+n+' image'+(n!==1?'s':''):'Delete Selected';
  hint.textContent=n>0?n+' selected':'Click to select · Drag ⠿ to reorder';
}}

function base(){{return window.top.location.href.split('?')[0]}}

function doDel(){{
  if(!sel.size)return;
  const p=new URLSearchParams();
  p.set('action','delete');p.set('folder',folder);
  p.set('keys',JSON.stringify(Array.from(sel)));
  window.top.location.href=base()+'?'+p.toString();
}}

function doSort(){{
  const keys=Array.from(grid.querySelectorAll('.card')).map(c=>c.dataset.key);
  const p=new URLSearchParams();
  p.set('action','sort_save');p.set('folder',folder);
  p.set('keys',JSON.stringify(keys));
  window.top.location.href=base()+'?'+p.toString();
}}

// ── Drag-and-drop reordering (Sortable.js) ────────────────────
Sortable.create(grid,{{
  animation:160,
  ghostClass:'sortable-ghost',
  chosenClass:'sortable-chosen',
  handle:'.drag-handle',
}});
</script>
</body>
</html>"""
    return html, height


# ---------------------------------------------------------------------------
# Browse — Finder helpers
# ---------------------------------------------------------------------------

def _render_folder_grid(
    prop_folders: list[str],
    all_objects: list[dict],
    master_objects: list[dict],
) -> None:
    """Render the top-level visual folder grid (Feature 1: hover-lift cards)."""

    # ── ⭐ Master Featured pinned card ─────────────────────────────────────
    n_master = len(master_objects)
    master_size = _fmt_bytes(sum(o.get("Size", 0) for o in master_objects))

    st.markdown("<div class='folder-card-btn master-card'>", unsafe_allow_html=True)
    if st.button(
        f"⭐\n{MASTER_FEATURED_PREFIX}\n{n_master} featured · {master_size}",
        key="open_master",
        use_container_width=True,
    ):
        st.session_state["browse_open_folder"] = f"{MASTER_FEATURED_PREFIX}/"
        st.session_state.pop(f"thumbs_{MASTER_FEATURED_PREFIX}/", None)
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='text-align:center;font-size:0.7rem;color:#bbb;margin:0 0 1.4rem'>🔒 Archive — protected</div>",
        unsafe_allow_html=True,
    )

    if not prop_folders:
        st.info("No property folders yet. Upload images to create one.")
        return

    st.markdown("<div class='section-label'>Property Folders</div>", unsafe_allow_html=True)

    # ── 4-column property folder grid ─────────────────────────────────────
    COLS = 4
    rows = [prop_folders[i:i + COLS] for i in range(0, len(prop_folders), COLS)]
    for row in rows:
        padded = row + [None] * (COLS - len(row))
        cols = st.columns(COLS)
        for col, folder in zip(cols, padded):
            if folder is None:
                continue
            with col:
                folder_name = folder.rstrip("/").split("/")[-1]
                folder_objs = [o for o in all_objects if o["Key"].startswith(folder)]
                n_items = len(folder_objs)
                folder_size = _fmt_bytes(sum(o.get("Size", 0) for o in folder_objs))

                # Feature 1: entire card is one button with hover lift
                st.markdown("<div class='folder-card-btn'>", unsafe_allow_html=True)
                if st.button(
                    f"📁\n{folder_name}\n{n_items} files · {folder_size}",
                    key=f"open_{folder}",
                    use_container_width=True,
                ):
                    st.session_state["browse_open_folder"] = folder
                    st.session_state.pop(f"thumbs_{folder}", None)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

                # Flat delete link below the card
                st.markdown("<div class='folder-del-btn'>", unsafe_allow_html=True)
                if st.button("🗑 Delete folder", key=f"grid_del_{folder}", use_container_width=True):
                    st.session_state["confirm_folder_delete"] = folder
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

    # ── Inline delete confirmation ─────────────────────────────────────────
    target = st.session_state.get("confirm_folder_delete")
    if target and target != f"{MASTER_FEATURED_PREFIX}/":
        folder_name = target.rstrip("/").split("/")[-1]
        n_del = len([o for o in all_objects if o["Key"].startswith(target)])
        st.warning(
            f"⚠ Delete all **{n_del} file(s)** from `{folder_name}/`? "
            f"Featured banners are safely archived in the Master folder."
        )
        ok_col, cancel_col = st.columns(2)
        with ok_col:
            if st.button("✅ Yes, delete folder", key="grid_confirm_yes", type="primary"):
                for o in all_objects:
                    if o["Key"].startswith(target):
                        delete_object(o["Key"])
                st.success(f"Deleted {n_del} file(s) from `{folder_name}/`.")
                st.session_state.pop("confirm_folder_delete", None)
                st.rerun()
        with cancel_col:
            if st.button("Cancel", key="grid_confirm_cancel"):
                st.session_state.pop("confirm_folder_delete", None)
                st.rerun()


def _render_folder_contents(folder: str, all_objects: list[dict]) -> None:
    """
    Render an opened folder view.
    Feature 2: click-to-highlight image selection via HTML component.
    Feature 3: drag-and-drop sort + sort_order.json persistence.
    """
    is_master = folder.startswith(f"{MASTER_FEATURED_PREFIX}/")
    folder_label = folder.rstrip("/").split("/")[-1]

    # ── Navigation header ──────────────────────────────────────────────────
    back_col, title_col = st.columns([1, 5])
    with back_col:
        if st.button("⬅ Back", key="back_to_grid", use_container_width=True):
            st.session_state.pop("browse_open_folder", None)
            st.session_state.pop(f"thumbs_{folder}", None)
            st.rerun()
    with title_col:
        icon = "⭐" if is_master else "📁"
        st.markdown(
            f"<h3 style='margin:0;padding-top:0.3rem'>{icon} {folder_label}</h3>",
            unsafe_allow_html=True,
        )

    objects = [o for o in all_objects if o["Key"].startswith(folder)]
    if not objects:
        st.info("This folder is empty.")
        return

    meta_by_key = {o["Key"]: o for o in objects}
    image_keys = [
        o["Key"] for o in objects
        if _is_image(o["Key"])
    ]
    other_keys = [
        o["Key"] for o in objects
        if not _is_image(o["Key"]) and not o["Key"].endswith("sort_order.json")
    ]
    total_size = sum(o.get("Size", 0) for o in objects)
    st.caption(f"{len(image_keys)} images · {_fmt_bytes(total_size)}")

    if not image_keys:
        st.info("No images in this folder.")
        if other_keys:
            with st.expander(f"Other files ({len(other_keys)})"):
                for k in other_keys:
                    st.write(f"`{k.split('/')[-1]}`")
        return

    # ── Load + apply saved sort order ─────────────────────────────────────
    saved_order = _load_sort_order(folder)
    has_custom_order = False
    if saved_order:
        existing_set = set(image_keys)
        ordered = [k for k in saved_order if k in existing_set]
        new_keys = [k for k in image_keys if k not in set(ordered)]
        image_keys = ordered + new_keys
        has_custom_order = True

    # ── Sort radio (overrides saved order for display only) ────────────────
    sort_col, rev_col = st.columns([4, 1])
    with sort_col:
        sort_mode = st.radio(
            "Sort",
            ["Filename A→Z", "Filename Z→A", "Date ↓", "Date ↑"],
            horizontal=True,
            key="browse_sort",
            label_visibility="collapsed",
        )
    with rev_col:
        if st.button("↕ Reverse", key="browse_rev_btn", use_container_width=True):
            st.session_state["browse_reversed"] = not st.session_state.get("browse_reversed", False)

    if sort_mode == "Filename A→Z":
        image_keys.sort()
        has_custom_order = False
    elif sort_mode == "Filename Z→A":
        image_keys.sort(reverse=True)
        has_custom_order = False
    elif sort_mode == "Date ↓":
        image_keys.sort(key=lambda k: str(meta_by_key[k].get("LastModified", "")), reverse=True)
        has_custom_order = False
    elif sort_mode == "Date ↑":
        image_keys.sort(key=lambda k: str(meta_by_key[k].get("LastModified", "")))
        has_custom_order = False

    if st.session_state.get("browse_reversed", False):
        image_keys.reverse()

    # ── Load thumbnails (session-cached) ──────────────────────────────────
    cache_key = f"thumbs_{folder}"
    if cache_key not in st.session_state:
        prog = st.progress(0, text=f"Loading {len(image_keys)} thumbnails…")
        thumbs: dict[str, str | None] = {}
        for i, key in enumerate(image_keys):
            thumbs[key] = _thumbnail_b64(key)
            prog.progress((i + 1) / len(image_keys), text=f"Loading {i + 1}/{len(image_keys)}…")
        prog.empty()
        st.session_state[cache_key] = thumbs
    else:
        thumbs = st.session_state[cache_key]
        # Ensure any newly sorted keys not yet cached are fetched
        missing = [k for k in image_keys if k not in thumbs]
        if missing:
            for k in missing:
                thumbs[k] = _thumbnail_b64(k)
            st.session_state[cache_key] = thumbs

    # ── Interactive image grid component (Feature 2 + 3) ─────────────────
    st.markdown("---")
    grid_html, grid_height = _build_image_grid_html(image_keys, thumbs, folder, has_custom_order)
    components.html(grid_html, height=grid_height, scrolling=False)

    # ── Folder wipe section ────────────────────────────────────────────────
    if not is_master:
        st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
        n_all = len(image_keys)
        if st.button(
            f"🗑️ Delete Entire Folder ({n_all} file{'s' if n_all != 1 else ''})",
            key="del_entire_folder",
            type="secondary",
        ):
            st.session_state["confirm_folder_delete"] = folder

        if st.session_state.get("confirm_folder_delete") == folder:
            st.warning(
                f"⚠ Permanently delete all **{n_all} image(s)** from `{folder_label}/`? "
                f"Featured banners are safe in `{MASTER_FEATURED_PREFIX}/`."
            )
            yes_col, no_col = st.columns(2)
            with yes_col:
                if st.button("✅ Yes, wipe folder", key="wipe_yes", type="primary"):
                    for o in objects:
                        delete_object(o["Key"])
                    st.session_state.pop("confirm_folder_delete", None)
                    st.session_state.pop("browse_open_folder", None)
                    st.session_state.pop(cache_key, None)
                    st.success(f"`{folder_label}/` wiped — {len(objects)} file(s) removed.")
                    st.rerun()
            with no_col:
                if st.button("Cancel", key="wipe_cancel"):
                    st.session_state.pop("confirm_folder_delete", None)
                    st.rerun()

    if other_keys:
        with st.expander(f"Other files ({len(other_keys)})"):
            for key in other_keys:
                meta = meta_by_key.get(key, {})
                st.write(f"`{key.split('/')[-1]}` — {_fmt_bytes(meta.get('Size', 0))}")
                st.markdown(f"[Presigned link (1h)]({presigned_url(key)})")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_browse() -> None:
    st.header("Browse bucket")

    # ── Handle component action callbacks (delete / sort_save) ─────────────
    # These arrive via window.top.location.href redirect from the HTML component.
    params = st.query_params
    action = params.get("action", "")

    if action == "delete":
        folder = params.get("folder", "")
        try:
            keys_to_delete: list[str] = json.loads(params.get("keys", "[]"))
            deleted = 0
            for k in keys_to_delete:
                try:
                    delete_object(k)
                    deleted += 1
                except Exception:
                    pass
            # Restore open-folder state and invalidate thumbnail cache
            if folder:
                st.session_state["browse_open_folder"] = folder
            st.session_state.pop(f"thumbs_{folder}", None)
            st.query_params.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Delete error: {exc}")
            st.query_params.clear()

    elif action == "sort_save":
        folder = params.get("folder", "")
        try:
            ordered_keys: list[str] = json.loads(params.get("keys", "[]"))
            if folder and ordered_keys:
                _save_sort_order(folder, ordered_keys)
            if folder:
                st.session_state["browse_open_folder"] = folder
            st.session_state.pop(f"thumbs_{folder}", None)
            st.query_params.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Sort save error: {exc}")
            st.query_params.clear()

    # ── Fetch all bucket objects ───────────────────────────────────────────
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

    st.markdown("<div style='margin:0.75rem 0 0.25rem'></div>", unsafe_allow_html=True)

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
            st.caption(f"Staged → `{MASTER_FEATURED_PREFIX}/{prefix or '<prefix>'}-featured.webp` ⭐")

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
            st.caption(f"Staged → `{prefix or '<prefix>'}-story-cover.{'gif' if src_ext_p == 'gif' else 'webp'}`")

    st.markdown("<div style='margin:1.25rem 0 0.5rem'></div>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        master_clicked = st.button(
            "🚀  Process & Upload Property Media Package",
            type="primary", use_container_width=True, disabled=not prefix,
        )

    if not master_clicked:
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
        st.markdown(f"<span style='font-weight:600;color:{SLATE}'>Portfolio Gallery</span>", unsafe_allow_html=True)
        with st.spinner("Checking existing sequence…"):
            start_seq = _next_gallery_seq(prefix)
        prog = st.progress(0, text="Processing gallery…")
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
            except Exception as exc:
                st.error(f"✗ {f.name}: {exc}")
                total_err += 1
        prog.progress(1.0, text="Gallery done")
        if start_seq > 1:
            st.caption(f"ℹ Continued from existing sequence — started at `{prefix}-{str(start_seq).zfill(2)}.webp`")

    if banner_file:
        st.markdown(f"<span style='font-weight:600;color:{CRIMSON}'>Featured Banner → Master Archive</span>", unsafe_allow_html=True)
        with st.spinner(f"Processing {banner_file.name}…"):
            try:
                raw = banner_file.read()
                data, _ = process_banner(raw, quality=banner_quality)
                r2_key = f"{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp"
                upload_object(r2_key, data, "image/webp")
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(f"✓ `{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp` — {_fmt_bytes(len(data))} ({savings:+.0f}%) ⭐")
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {banner_file.name}: {exc}")
                total_err += 1

    if story_file:
        st.markdown(f"<span style='font-weight:600;color:{BLACK}'>Story Cover</span>", unsafe_allow_html=True)
        with st.spinner(f"Processing {story_file.name}…"):
            try:
                raw = story_file.read()
                src_ext = story_file.name.rsplit(".", 1)[-1].lower()
                data, out_ext = process_story_cover(raw, quality=story_quality, src_ext=src_ext)
                ct = "image/gif" if out_ext == "gif" else "image/webp"
                r2_key = f"properties/{prefix}/{prefix}-story-cover.{out_ext}"
                upload_object(r2_key, data, ct)
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(f"✓ `{prefix}-story-cover.{out_ext}` — {_fmt_bytes(len(data))} ({savings:+.0f}%)")
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {story_file.name}: {exc}")
                total_err += 1

    st.markdown("---")
    if total_err == 0:
        st.success(f"🎉 Package complete — {total_ok} file(s) uploaded.")
    else:
        st.warning(f"{total_ok} uploaded · {total_err} failed — check errors above.")


def page_settings() -> None:
    st.header("Connection settings")
    st.info("Credentials are loaded from Streamlit secrets (Streamlit Cloud) or environment variables (Replit/local).")

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
        st.sidebar.markdown("<div style='text-align:center;font-size:3rem'>🧭</div>", unsafe_allow_html=True)

    st.sidebar.markdown(
        f"<h2 style='color:{CRIMSON};margin-top:0.25rem;text-align:center;font-size:1.15rem'>"
        f"{APP_NAME}</h2>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<hr class='sidebar-divider'>", unsafe_allow_html=True)

    page = st.sidebar.radio(
        "Navigate", ["Browse bucket", "Upload Images", "Settings"],
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
    elif page == "Settings":
        page_settings()


if __name__ == "__main__":
    main()
