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
/* ── Global ────────────────────────────────────────────────── */
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

/* ── Upload column boxes ─────────────────────────────────── */
.upload-col{{border:1px solid {SLATE};border-radius:8px;padding:1rem}}
</style>
""", unsafe_allow_html=True)


def _col_header(label: str, spec: str, color: str) -> None:
    st.markdown(
        f"<div style='border-top:3px solid {color};padding-top:.6rem;margin-bottom:.6rem'>"
        f"<span style='font-weight:700;font-size:.95rem;color:{color}'>{label}</span><br>"
        f"<span style='font-size:.75rem;color:#888'>{spec}</span></div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Mac Finder folder grid — HTML component
# ---------------------------------------------------------------------------

def _build_folder_grid_html(
    prop_folders: list[str],
    folder_meta: dict[str, dict],
    master_n: int,
    master_size: str,
) -> tuple[str, int]:
    """
    Render a Mac Finder-style 4-column folder grid.
    • Click card body   → open folder  (?action=open_folder)
    • Click ○ circle    → toggle select
    • Selected ≥ 1      → 'Delete Selected Folders' button appears
    Returns (html_string, iframe_height_px).
    """

    def card_html(folder_key: str, icon: str, name: str, meta_line: str,
                  extra_class: str = "") -> str:
        fk = json.dumps(folder_key)
        return f"""<div class="card {extra_class}" data-folder={fk}
     onclick="openFolder({fk})">
  <div class="circle" onclick="event.stopPropagation();toggleSel(this,{fk})"></div>
  <div class="icon">{icon}</div>
  <div class="name">{name}</div>
  <div class="meta">{meta_line}</div>
</div>"""

    cards = card_html(
        f"{MASTER_FEATURED_PREFIX}/", "⭐", "Master Featured",
        f"{master_n} files · {master_size}", "master",
    )
    for folder in prop_folders:
        m = folder_meta.get(folder, {})
        name = folder.rstrip("/").split("/")[-1]
        cards += card_html(folder, "📁", name,
                           f"{m.get('n', 0)} files · {m.get('size_str', '0 B')}")

    total_cards = 1 + len(prop_folders)
    rows = max(1, (total_cards + 3) // 4)
    height = rows * 220 + 74  # 220px per row + 74px header buffer

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0;
   font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif}}
body{{padding:10px;background:transparent}}

/* ── Header bar (hidden until selection) ── */
#hdr{{
  display:none;align-items:center;gap:10px;
  padding:0 4px 12px;
}}
#del-btn{{
  background:#990000;color:#fff;border:none;border-radius:8px;
  padding:7px 16px;font-size:.8rem;font-weight:600;cursor:pointer;
  transition:background .14s;
}}
#del-btn:hover{{background:#b80000}}
#del-btn.confirm{{background:#cc0000}}
#sel-lbl{{font-size:.78rem;color:#888}}

/* ── Folder grid ── */
#grid{{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:14px;
}}

/* ── Folder card ── */
.card{{
  position:relative;
  display:flex;flex-direction:column;align-items:center;
  justify-content:center;
  padding:26px 10px 20px;
  background:#fafafa;
  border:1.5px solid #eaeaea;
  border-radius:16px;
  cursor:pointer;
  transition:transform .16s ease,box-shadow .16s ease,
             border-color .16s ease,background .16s ease;
  user-select:none;
  min-height:190px;
}}
.card:hover{{
  transform:translateY(-5px);
  box-shadow:0 10px 30px rgba(0,0,0,.10);
  border-color:#c8c8c8;background:#fff;
}}
.card.master{{border-color:#f0d0d0;background:#fff9f9}}
.card.master:hover{{
  box-shadow:0 10px 30px rgba(153,0,0,.12);
  border-color:#d08080;background:#fff4f4;
}}
.card.selected{{
  border-color:#990000!important;
  background:#fff5f5!important;
  box-shadow:0 0 0 3px rgba(153,0,0,.14)!important;
}}

/* ── Folder icon ── */
.icon{{font-size:3.6rem;line-height:1;margin-bottom:12px}}

/* ── Labels ── */
.name{{
  font-size:.84rem;font-weight:600;color:#1a1a1a;
  text-align:center;line-height:1.3;margin-bottom:5px;
  word-break:break-word;
}}
.meta{{font-size:.71rem;color:#b0b0b0;text-align:center}}

/* ── Selection circle ── */
.circle{{
  position:absolute;top:11px;right:11px;
  width:21px;height:21px;border-radius:50%;
  border:1.5px solid #d0d0d0;background:#fff;
  cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  opacity:0;transition:opacity .14s,background .14s,border-color .14s;
  z-index:2;
}}
.card:hover .circle{{opacity:1}}
.circle.on{{opacity:1!important;background:#990000;border-color:#990000}}
.circle.on::after{{content:'✓';color:#fff;font-size:.64rem;font-weight:800}}
</style>
</head>
<body>

<div id="hdr">
  <button id="del-btn" onclick="handleDelete()">🗑 Delete Selected Folders</button>
  <span id="sel-lbl"></span>
</div>

<div id="grid">
{cards}
</div>

<script>
const hdr=document.getElementById('hdr');
const delBtn=document.getElementById('del-btn');
const selLbl=document.getElementById('sel-lbl');
const sel=new Set();
let confirmPending=false;

function toggleSel(circle,folder){{
  if(sel.has(folder)){{
    sel.delete(folder);
    circle.classList.remove('on');
    circle.closest('.card').classList.remove('selected');
  }}else{{
    sel.add(folder);
    circle.classList.add('on');
    circle.closest('.card').classList.add('selected');
  }}
  const n=sel.size;
  hdr.style.display=n>0?'flex':'none';
  selLbl.textContent=n+' folder'+(n!==1?'s':'')+' selected';
  confirmPending=false;
  delBtn.textContent='🗑 Delete Selected Folders';
  delBtn.classList.remove('confirm');
}}

function openFolder(folder){{
  const p=new URLSearchParams();
  p.set('action','open_folder');
  p.set('folder',folder);
  window.parent.location.href=base()+'?'+p.toString();
}}

function handleDelete(){{
  if(!sel.size)return;
  if(!confirmPending){{
    confirmPending=true;
    delBtn.textContent='⚠ Tap again to confirm';
    delBtn.classList.add('confirm');
    setTimeout(()=>{{
      confirmPending=false;
      delBtn.textContent='🗑 Delete Selected Folders';
      delBtn.classList.remove('confirm');
    }},3500);
    return;
  }}
  const p=new URLSearchParams();
  p.set('action','delete_folders');
  p.set('folders',JSON.stringify(Array.from(sel)));
  window.parent.location.href=base()+'?'+p.toString();
}}

function base(){{return window.parent.location.href.split('?')[0]}}
</script>
</body>
</html>"""
    return html, height


# ---------------------------------------------------------------------------
# Image grid — HTML component (16:9, top action bar, drag-and-drop)
# ---------------------------------------------------------------------------

def _build_image_grid_html(
    image_keys: list[str],
    thumbs: dict[str, str | None],
    folder: str,
    has_custom_order: bool,
) -> tuple[str, int]:
    """
    Interactive image panel:
      • 16:9 aspect-ratio thumbnail containers (object-fit cover)
      • Action bar pinned to TOP: [Delete Selected] [status] [Apply Sort Order]
      • Click card = toggle crimson selection border
      • ⠿ drag handle = Sortable.js reorder
      • Actions redirect window.top with ?action=delete|sort_save
    Returns (html_string, iframe_height_px).
    """
    cards = ""
    for key in image_keys:
        b64 = thumbs.get(key)
        filename = key.split("/")[-1]
        fk = json.dumps(key)
        if b64:
            img_inner = (
                f"<img src='data:image/jpeg;base64,{b64}' "
                f"style='position:absolute;top:0;left:0;width:100%;height:100%;"
                f"object-fit:cover;pointer-events:none'>"
            )
        else:
            img_inner = (
                "<div style='position:absolute;top:0;left:0;width:100%;height:100%;"
                "display:flex;align-items:center;justify-content:center;"
                "color:#ddd;font-size:1.4rem'>⚠</div>"
            )
        cards += f"""
<div class="card" data-key={fk}>
  <div class="handle">⠿ ⠿</div>
  <div class="ratio16x9">{img_inner}</div>
  <div class="fname">{filename}</div>
</div>"""

    n = len(image_keys)
    rows = max(1, (n + 2) // 3)
    # 56.25% padding = 16:9; at ~300px col width each row ≈ 169px image
    # + 22px handle + 22px fname + 10px gaps = ~223px per row
    height = rows * 223 + 70  # +70 for top bar + body padding

    order_badge = (
        "<span style='font-size:.7rem;color:#708090;margin-left:4px'>📌 Custom order</span>"
        if has_custom_order else ""
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;
   font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif}}
body{{padding:8px;background:transparent}}

/* ── Top action bar (always visible) ── */
.topbar{{
  display:flex;align-items:center;gap:8px;
  padding:7px 10px;margin-bottom:10px;
  background:#f6f6f6;border-radius:10px;border:1px solid #ebebeb;
}}
.btn{{border:none;border-radius:7px;padding:6px 14px;
  font-size:.78rem;font-weight:600;cursor:pointer;transition:opacity .14s}}
.btn:hover{{opacity:.82}}
.btn-del{{background:#990000;color:#fff}}
.btn-del:disabled{{background:#e4e4e4;color:#bbb;cursor:default;opacity:1}}
.btn-sort{{background:#708090;color:#fff;margin-left:auto}}
.hint{{font-size:.73rem;color:#bbb;flex:1}}

/* ── Image grid ── */
#grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}

/* ── Image card ── */
.card{{
  border-radius:10px;overflow:hidden;cursor:pointer;
  border:3px solid transparent;
  background:#f2f2f2;
  transition:border-color .15s,box-shadow .15s;
  user-select:none;
}}
.card:hover{{box-shadow:0 4px 16px rgba(0,0,0,.10)}}
.card.sel{{
  border-color:#990000;
  box-shadow:0 0 0 1px rgba(153,0,0,.18);
}}

/* ── Drag handle strip ── */
.handle{{
  text-align:center;padding:4px 0;
  color:#d0d0d0;font-size:.72rem;letter-spacing:3px;
  cursor:grab;background:#f8f8f8;
  border-bottom:1px solid #ebebeb;
  transition:color .12s,background .12s;
}}
.handle:hover{{color:#999;background:#f0f0f0}}
.handle:active{{cursor:grabbing}}

/* ── 16:9 image container ── */
.ratio16x9{{
  position:relative;
  width:100%;
  padding-bottom:56.25%;
  overflow:hidden;
  background:#ebebeb;
}}

/* ── Filename caption ── */
.fname{{
  font-size:.66rem;color:#999;padding:4px 7px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  background:#fafafa;border-top:1px solid #f0f0f0;
}}

/* ── Sortable states ── */
.sortable-ghost{{opacity:.28;border:2px dashed #ccc}}
.sortable-chosen{{
  transform:scale(1.03);
  box-shadow:0 12px 32px rgba(0,0,0,.18)!important;
  z-index:99;
}}
</style>
</head>
<body>

<!-- TOP ACTION BAR -->
<div class="topbar">
  <button class="btn btn-del" id="bdel" disabled onclick="doDel()">Delete Selected</button>
  <span class="hint" id="hint">Click photo to select &nbsp;·&nbsp; Drag ⠿ to reorder</span>
  {order_badge}
  <button class="btn btn-sort" onclick="doSort()">💾 Apply Sort Order</button>
</div>

<!-- IMAGE GRID -->
<div id="grid" data-folder={json.dumps(folder)}>
{cards}
</div>

<script>
const grid=document.getElementById('grid');
const bdel=document.getElementById('bdel');
const hint=document.getElementById('hint');
const folder=grid.dataset.folder;
const sel=new Set();

// ── Click-to-highlight ──────────────────────────────────────
grid.querySelectorAll('.card').forEach(c=>{{
  c.addEventListener('click',e=>{{
    if(e.target.closest('.handle'))return;
    const k=c.dataset.key;
    if(sel.has(k)){{sel.delete(k);c.classList.remove('sel')}}
    else{{sel.add(k);c.classList.add('sel')}}
    updateBar();
  }});
}});

function updateBar(){{
  const n=sel.size;
  bdel.disabled=n===0;
  bdel.textContent=n>0?'🗑 Delete '+n+' photo'+(n!==1?'s':''):'Delete Selected';
  hint.textContent=n>0?n+' selected':'Click photo to select · Drag ⠿ to reorder';
}}

// ── Action redirects ────────────────────────────────────────
function base(){{return window.parent.location.href.split('?')[0]}}

function doDel(){{
  if(!sel.size)return;
  const p=new URLSearchParams();
  p.set('action','delete');p.set('folder',folder);
  p.set('keys',JSON.stringify(Array.from(sel)));
  window.parent.location.href=base()+'?'+p.toString();
}}

function doSort(){{
  const keys=Array.from(grid.querySelectorAll('.card')).map(c=>c.dataset.key);
  const p=new URLSearchParams();
  p.set('action','sort_save');p.set('folder',folder);
  p.set('keys',JSON.stringify(keys));
  window.parent.location.href=base()+'?'+p.toString();
}}

// ── Drag-and-drop (Sortable.js) ─────────────────────────────
Sortable.create(grid,{{
  animation:160,
  handle:'.handle',
  ghostClass:'sortable-ghost',
  chosenClass:'sortable-chosen',
}});
</script>
</body>
</html>"""
    return html, height


# ---------------------------------------------------------------------------
# Browse — Finder folder grid view
# ---------------------------------------------------------------------------

def _render_folder_grid(
    prop_folders: list[str],
    all_objects: list[dict],
    master_objects: list[dict],
) -> None:
    master_n = len(master_objects)
    master_size = _fmt_bytes(sum(o.get("Size", 0) for o in master_objects))

    # Build folder metadata dict
    folder_meta: dict[str, dict] = {}
    for folder in prop_folders:
        objs = [o for o in all_objects if o["Key"].startswith(folder)]
        folder_meta[folder] = {
            "n": len(objs),
            "size_str": _fmt_bytes(sum(o.get("Size", 0) for o in objs)),
        }

    grid_html, grid_height = _build_folder_grid_html(
        prop_folders, folder_meta, master_n, master_size
    )
    components.html(grid_html, height=grid_height, scrolling=False)


# ---------------------------------------------------------------------------
# Browse — Opened folder view
# ---------------------------------------------------------------------------

def _render_folder_contents(folder: str, all_objects: list[dict]) -> None:
    is_master = folder.startswith(f"{MASTER_FEATURED_PREFIX}/")
    folder_label = folder.rstrip("/").split("/")[-1]

    objects = [o for o in all_objects if o["Key"].startswith(folder)]
    meta_by_key = {o["Key"]: o for o in objects}
    image_keys = [o["Key"] for o in objects if _is_image(o["Key"])]
    other_keys = [
        o["Key"] for o in objects
        if not _is_image(o["Key"]) and not o["Key"].endswith("sort_order.json")
    ]
    total_size = sum(o.get("Size", 0) for o in objects)

    # ── Sticky top header row ───────────────────────────────────────────────
    icon = "⭐" if is_master else "📁"
    back_col, title_col, action_col = st.columns([1, 5, 2])

    with back_col:
        if st.button("⬅ Back", key="back_btn", use_container_width=True):
            st.session_state.pop("browse_open_folder", None)
            st.session_state.pop(f"thumbs_{folder}", None)
            st.rerun()

    with title_col:
        st.markdown(
            f"<h3 style='margin:0;padding-top:.25rem'>{icon} {folder_label}</h3>"
            f"<span style='font-size:.76rem;color:#aaa'>"
            f"{len(image_keys)} images · {_fmt_bytes(total_size)}</span>",
            unsafe_allow_html=True,
        )

    with action_col:
        if not is_master and image_keys:
            if st.button("🗑️ Clear Folder", key="clear_folder_btn",
                         use_container_width=True, type="secondary"):
                st.session_state["confirm_wipe"] = folder

    st.markdown("<div style='margin:.4rem 0'></div>", unsafe_allow_html=True)

    # ── Clear-folder confirmation ───────────────────────────────────────────
    if st.session_state.get("confirm_wipe") == folder:
        st.warning(
            f"⚠ Permanently wipe all **{len(image_keys)} image(s)** from `{folder_label}/`? "
            f"Featured banners are safe in `{MASTER_FEATURED_PREFIX}/`."
        )
        y_col, n_col = st.columns(2)
        with y_col:
            if st.button("✅ Yes, wipe", key="wipe_yes", type="primary"):
                for o in objects:
                    delete_object(o["Key"])
                st.session_state.pop("confirm_wipe", None)
                st.session_state.pop("browse_open_folder", None)
                st.session_state.pop(f"thumbs_{folder}", None)
                st.success(f"`{folder_label}/` cleared — {len(objects)} file(s) removed.")
                st.rerun()
        with n_col:
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

    # ── Sort controls ───────────────────────────────────────────────────────
    s_col, r_col = st.columns([5, 1])
    with s_col:
        sort_mode = st.radio(
            "Sort", ["Custom order", "Filename A→Z", "Filename Z→A", "Date ↓", "Date ↑"],
            horizontal=True, key="browse_sort", label_visibility="collapsed",
        )
    with r_col:
        if st.button("↕ Reverse", key="rev_btn", use_container_width=True):
            st.session_state["browse_reversed"] = not st.session_state.get("browse_reversed", False)

    # ── Apply sort order ────────────────────────────────────────────────────
    saved_order = _load_sort_order(folder)
    has_custom_order = bool(saved_order)

    if sort_mode == "Custom order" and saved_order:
        existing_set = set(image_keys)
        ordered = [k for k in saved_order if k in existing_set]
        new_keys = [k for k in image_keys if k not in set(ordered)]
        image_keys = ordered + new_keys
    elif sort_mode == "Filename A→Z":
        image_keys.sort()
    elif sort_mode == "Filename Z→A":
        image_keys.sort(reverse=True)
    elif sort_mode == "Date ↓":
        image_keys.sort(key=lambda k: str(meta_by_key[k].get("LastModified", "")), reverse=True)
    elif sort_mode == "Date ↑":
        image_keys.sort(key=lambda k: str(meta_by_key[k].get("LastModified", "")))

    if st.session_state.get("browse_reversed", False):
        image_keys.reverse()

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
            with st.spinner(f"Loading {len(missing)} new images…"):
                for k in missing:
                    thumbs[k] = _thumbnail_b64(k)
            st.session_state[cache_key] = thumbs

    # ── Interactive image grid ──────────────────────────────────────────────
    grid_html, grid_height = _build_image_grid_html(
        image_keys, thumbs, folder, has_custom_order
    )
    components.html(grid_html, height=grid_height, scrolling=False)

    # ── Other files ─────────────────────────────────────────────────────────
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

    # ── Query-param action router ───────────────────────────────────────────
    params = st.query_params
    action = params.get("action", "")

    if action == "open_folder":
        folder = params.get("folder", "")
        if folder:
            st.session_state["browse_open_folder"] = folder
            st.session_state.pop(f"thumbs_{folder}", None)
        st.query_params.clear()
        st.rerun()

    elif action == "delete":
        folder = params.get("folder", "")
        try:
            keys_to_delete: list[str] = json.loads(params.get("keys", "[]"))
            for k in keys_to_delete:
                try:
                    delete_object(k)
                except Exception:
                    pass
        except Exception:
            pass
        if folder:
            st.session_state["browse_open_folder"] = folder
        st.session_state.pop(f"thumbs_{folder}", None)
        st.query_params.clear()
        st.rerun()

    elif action == "sort_save":
        folder = params.get("folder", "")
        try:
            ordered_keys: list[str] = json.loads(params.get("keys", "[]"))
            if folder and ordered_keys:
                _save_sort_order(folder, ordered_keys)
        except Exception:
            pass
        if folder:
            st.session_state["browse_open_folder"] = folder
        st.session_state.pop(f"thumbs_{folder}", None)
        st.query_params.clear()
        st.rerun()

    elif action == "delete_folders":
        try:
            folders: list[str] = json.loads(params.get("folders", "[]"))
            for fld in folders:
                if fld.startswith(f"{MASTER_FEATURED_PREFIX}/"):
                    continue  # never delete master archive
                for obj in list_objects(prefix=fld):
                    try:
                        delete_object(obj["Key"])
                    except Exception:
                        pass
                st.session_state.pop(f"thumbs_{fld}", None)
        except Exception:
            pass
        st.query_params.clear()
        st.rerun()

    # ── Fetch all bucket objects ─────────────────────────────────────────────
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
            st.caption(
                f"ℹ Continued from existing sequence — "
                f"started at `{prefix}-{str(start_seq).zfill(2)}.webp`"
            )

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
            f"<img src='data:image/png;base64,{_b64}' "
            f"style='width:100%;border-radius:8px;'>",
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
