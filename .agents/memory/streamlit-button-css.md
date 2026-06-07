---
name: Streamlit 1.57 button CSS architecture
description: How to build Mac Finder-style cards with native Streamlit buttons without \n label support
---

## The Problem
`st.button("line1\nline2")` — Streamlit collapses \n to a space in the rendered DOM.
`white-space: pre-line` on `button p` does NOT restore line breaks because the newline
character is lost before React renders the label.

## The Solution
Split the "card" into three parts rendered sequentially in the same column:

```python
st.markdown("<div class='finder-card'><div class='finder-card-icon'>📁</div>", unsafe_allow_html=True)
if st.button(folder_name, key=..., use_container_width=True):
    ...open folder...
st.markdown(f"<div class='finder-card-meta'>{n} files · {size}</div></div>", unsafe_allow_html=True)
```

CSS then makes `div.finder-card` the styled card shell, and `div.finder-card button`
is styled as transparent (no border, no bg) so the whole area looks like one card.
The hover lift effect goes on the div.finder-card, not the button.

**Why:** Streamlit markdown divs and adjacent widget containers appear as siblings
in the DOM. Even though the closing `</div>` is in a separate st.markdown() call,
the browser's HTML parser closes the open div correctly when it encounters the tag.

**How to apply:** Any time you want a card with multiline content where one line is
a clickable Streamlit widget — use this split structure. The emoji/icon lives in
a markdown div above, the button inside is transparent, meta text below.

## Also: st.components.v1.html() removed June 2026
Never use it. Use native st.columns() + st.button() + st.checkbox() + st.markdown()
for all Browse UI. Image selection = st.button() toggle that writes to session_state.
Numeric sort = st.number_input() per image + "Save Order" reads positions + saves JSON.
