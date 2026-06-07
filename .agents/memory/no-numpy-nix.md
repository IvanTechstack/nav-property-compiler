---
name: No numpy in Nix — PIL base64 workaround
description: Why st.image(), st.sidebar.image(), and st.table() are banned in this codebase
---

## The rule
Never use `st.image()`, `st.sidebar.image()`, or `st.table()` in this Streamlit app.
Instead, encode images with PIL to base64 JPEG and inject via `st.markdown()` HTML `<img>` tags.

## Why
Streamlit's `st.image()` and `st.sidebar.image()` call numpy internally to handle image arrays.
The numpy C extension (`_multiarray_umath.so`) requires `libstdc++.so.6` which is missing
in the Nix sandbox, causing a crash at import time.
`st.table()` also calls numpy. These are silent failures that surface as traceback errors.

## How to apply
Pattern for any image display:
```python
import base64
from PIL import Image
from io import BytesIO

buf = BytesIO()
img.save(buf, format="JPEG", quality=85)
b64 = base64.b64encode(buf.getvalue()).decode()
st.markdown(f'<img src="data:image/jpeg;base64,{b64}" style="width:100%"/>', unsafe_allow_html=True)
```
For the sidebar avatar, same pattern but use `st.sidebar.markdown(...)`.
For tables, build a markdown string with `|col|col|` syntax and pass to `st.markdown()`.
