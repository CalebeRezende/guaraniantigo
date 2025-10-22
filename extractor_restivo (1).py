#!/usr/bin/env python3
# -*- coding: utf-8 -*-
\"\"\"
Guarani↔Spanish extractor for Restivo/Montoya 'Arte de la Lengua Guaraní' (1724, CELIA 2010 edition).
Usage:
  python extractor_restivo.py --pdf 1724.pdf --outdir out_restivo --min_conf 3

It tries pdfminer.six first (better for OCR-like PDFs), falls back to PyPDF2.
It scans a wide variety of patterns beyond quotes: ut:, vel:, dice/significa, '=' and colon lists,
guillemets «», curly quotes ‘ ’ “ ”, etc. It supports multiline capture with configurable context.
Saves: raw matches (ALL), deduplicated CSV, and a high-confidence subset.

Author: (for Calebe Rezende)
\"\"\"
import re
import os
import sys
import csv
import json
import argparse
from typing import List, Tuple, Dict, Any

def read_pdf_text(pdf_path: str) -> Tuple[str, List[int]]:
    \"\"\"
    Returns (full_text_with_page_markers, page_offsets). Each page begins with '\\n[[[PAGE N]]]\\n'.
    page_offsets[i] = char offset at start of page i (0-based index).
    \"\"\"
    text_pages: List[str] = []
    page_offsets: List[int] = []
    source = None

    # Try pdfminer.six
    try:
        from pdfminer.high_level import extract_text
        txt = extract_text(pdf_path) or ""
        # Split by formfeed; not perfectly page-accurate but good enough; add markers
        # We'll also try to re-split if formfeeds are not present by creating pseudo-pages of ~8k chars
        pages = txt.split("\\f")
        if len(pages) <= 1:
            # fallback: pseudo-paging in blocks
            chunk = 8000
            pages = [txt[i:i+chunk] for i in range(0, len(txt), chunk)] or [""]
        text_pages = pages
        source = "pdfminer.six"
    except Exception as e:
        # Fallback to PyPDF2
        try:
            import PyPDF2
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for i, page in enumerate(reader.pages):
                    try:
                        t = page.extract_text() or ""
                    except Exception:
                        t = ""
                    text_pages.append(t)
            source = "PyPDF2"
        except Exception as e2:
            print(f"[FATAL] Could not read PDF with pdfminer or PyPDF2: {e2}", file=sys.stderr)
            return "", []

    # Build full_text with explicit page markers
    full_text = ""
    offset = 0
    for i, t in enumerate(text_pages):
        page_offsets.append(offset)
        full_text += f"\\n[[[PAGE {i+1}]]]\\n" + (t or "")
        offset = len(full_text)

    print(f"[INFO] Read {len(text_pages)} pages using {source}. Full length: {len(full_text)} chars.", file=sys.stderr)
    return full_text, page_offsets

# ---- Heuristics ----

SPANISH_WORD_CUES = [
    " el ", " la ", " los ", " las ", " de ", " del ", " al ",
    " por ", " en ", " mi ", " tu ", " su ", " que ", " como ",
    " este ", " esa ", " eso ", " aquel ", " aquella ", " aquello "
]

def clean_spaces(s: str) -> str:
    import re as _re
    return _re.sub(r"\\s+", " ", s or "").strip()

def is_spanish_like(s: str) -> bool:
    s_low = (" " + (s or "").lower() + " ")
    if any(cue in s_low for cue in SPANISH_WORD_CUES):
        return True
    import re as _re
    if _re.search(r"[áéíóúñ]", s_low):
        return True
    # Spanish glosses commonly have spaces and lack nasal diacritics of Guarani
    if " " in s_low and not _re.search(r"[ãẽĩõũỹ’]", s_low):
        return True
    return False

def is_guarani_like(s: str) -> bool:
    import re as _re
    s_low = (s or "").lower()
    if _re.search(r"[ãẽĩõũỹ’ý']", s_low):
        return True
    if any(s_low.startswith(w) for w in ["che","nde","tupã","ava","mba'e","ha'e","ñande","ore","peẽ","ko","upe","ahe","aj","mba'"]):
        return True
    # y as vowel, apostrophes, short tokens (1–4 words)
    if ("y" in s_low or "’" in s_low or "'" in s_low) and len(s_low.split()) <= 4:
        return True
    return False

def looks_clean_gua(g: str) -> bool:
    import re as _re
    g = (g or "").strip()
    if not g:
        return False
    if g[0] in "'»«“”‘’.,;:!?[](){}":
        return False
    if not _re.search(r"[A-Za-zãẽĩõũỹ’ý]", g):
        return False
    if len(g) > 60:
        return False
    if g[0].isdigit() or g[0] in "[":
        return False
    return True

def looks_clean_spa(e: str) -> bool:
    import re as _re
    e = (e or "").strip(" .;:,'‘’“”»«")
    if len(e) < 2:
        return False
    if not _re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]", e):
        return False
    if len(e) > 120:
        return False
    return True

def confidence_score(g: str, e: str, rule: str) -> int:
    conf = 1
    if is_guarani_like(g): conf += 1
    if is_spanish_like(e): conf += 1
    if " " in (e or ""): conf += 1
    # rule-specific boosts
    if rule in ("ut_quotes","ut_equal","dice_significa","corresponde_equivale","colon_list","quotes"):
        conf += 1
    return conf

def page_of(idx: int, page_offsets: List[int]) -> int:
    # binary search
    import bisect
    i = bisect.bisect_right(page_offsets, idx) - 1
    return (i + 1) if i >= 0 else 1

def add_pair(pairs: List[Dict[str, Any]], start_idx: int, g: str, e: str, rule: str,
             full_text: str, page_offsets: List[int],
             left: str = "", right: str = ""):
    g2, e2 = clean_spaces(g), clean_spaces(e)
    if not g2 or not e2: return
    if not looks_clean_gua(g2): return
    if not looks_clean_spa(e2): return
    if is_spanish_like(g2) and not is_guarani_like(g2): return  # avoid Spanish on Guarani side

    p = page_of(start_idx, page_offsets)
    conf = confidence_score(g2, e2, rule)
    pairs.append({
        "page": p,
        "guarani": g2.strip(" .;:,'‘’“”»«"),
        "espanhol": e2.strip(" .;:,'‘’“”»«"),
        "left_context": clean_spaces(left)[-160:],
        "right_context": clean_spaces(right)[:160],
        "rule": rule,
        "confidence": conf
    })

def mine_pairs(full_text: str, page_offsets: List[int], ctx_chars: int = 140) -> List[Dict[str, Any]]:
    t = full_text
    pairs: List[Dict[str, Any]] = []

    # 1) Quotes (supports ‘ ’, ’ ', “ ”, » «)
    q_pattern = re.compile(r"([^\n\r]{0,160})[‘'“»]([^’'”«]+)[’'”«]([^\n\r]{0,160})")
    for m in q_pattern.finditer(t):
        left, gloss, right = m.group(1), m.group(2), m.group(3)
        # left token as candidate Guarani
        lt = re.split(r"[\\s,;:\\(\\)\\[\\]\\{\\}—–\\-]+", left.strip())[-1] if left.strip() else ""
        if is_guarani_like(lt) and is_spanish_like(gloss):
            add_pair(pairs, m.start(), lt, gloss, "quotes", t, page_offsets, left, right)

    # 2) ut: sequences (DOTALL window, cut on page marker to avoid bleeding too far)
    ut_pat = re.compile(r"ut:\\s*(.{1,600})", re.IGNORECASE | re.DOTALL)
    for m in ut_pat.finditer(t):
        seg = m.group(1)
        cut = seg.split("[[[PAGE")[0]
        seg = cut
        for piece in re.split(r";", seg):
            # a) word , 'translation'
            for m2 in re.finditer(r"([A-Za-zÁÉÍÓÚáéíóúÑñãẽĩõũỹ’'.\\- ]{1,60}),\\s*[‘'“]([^’'”]+)[’'”]", piece):
                g, e = m2.group(1).strip(" ."), m2.group(2)
                if is_guarani_like(g) and is_spanish_like(e):
                    add_pair(pairs, m.start(), g, e, "ut_quotes", t, page_offsets, seg[:ctx_chars], seg[ctx_chars:ctx_chars*2])
            # b) word = translation
            for m3 in re.finditer(r"([A-Za-zãẽĩõũỹ’'.\\- ]{1,60})\\s*=\\s*([A-Za-zÁÉÍÓÚáéíóúÑñ ,;:.']{1,120})", piece):
                g, e = m3.group(1), m3.group(2)
                if is_guarani_like(g) and is_spanish_like(e):
                    add_pair(pairs, m.start(), g, e, "ut_equal", t, page_offsets, seg[:ctx_chars], seg[ctx_chars:ctx_chars*2])

    # 3) dice / dícese / significa
    pat_dice = re.compile(r"([A-Za-zãẽĩõũỹ’'.\\- ]{1,60})\\s*,?\\s*(?:dice|dícese|significa)\\s*[: ]\\s*[‘'“]([^’'”]+)[’'”]",
                          re.IGNORECASE)
    for m in pat_dice.finditer(t):
        g, e = m.group(1), m.group(2)
        if is_guarani_like(g) and is_spanish_like(e):
            start = m.start()
            left = t[max(0, start-ctx_chars):start]
            right = t[m.end():m.end()+ctx_chars]
            add_pair(pairs, start, g, e, "dice_significa", t, page_offsets, left, right)

    # 4) corresponde al / equivale a
    pat_corresp = re.compile(r"([A-Za-zãẽĩõũỹ’'.\\- ]{1,60})\\s*,?\\s*(?:corresponde al|equivale a)\\s*[: ]\\s*([A-Za-zÁÉÍÓÚáéíóúÑñ ,;:.']{1,120})",
                             re.IGNORECASE)
    for m in pat_corresp.finditer(t):
        g, e = m.group(1), m.group(2)
        if is_guarani_like(g) and is_spanish_like(e):
            start = m.start()
            left = t[max(0, start-ctx_chars):start]
            right = t[m.end():m.end()+ctx_chars]
            add_pair(pairs, start, g, e, "corresponde_equivale", t, page_offsets, left, right)

    # 5) Colon lists: lines like "palabra, 'glosa'"
    pat_colon = re.compile(r"(^|[\\.\\n:])\\s*([A-Za-zãẽĩõũỹ’'.\\- ]{1,60}),\\s*[‘'“]([^’'”]+)[’'”]")
    for m in pat_colon.finditer(t):
        g, e = m.group(2), m.group(3)
        if is_guarani_like(g) and is_spanish_like(e):
            start = m.start()
            left = t[max(0, start-ctx_chars):start]
            right = t[m.end():m.end()+ctx_chars]
            add_pair(pairs, start, g, e, "colon_list", t, page_offsets, left, right)

    return pairs

def save_csv(rows: List[Dict[str, Any]], out_csv: str):
    if not rows:
        print(f"[WARN] No rows to save: {out_csv}", file=sys.stderr)
        return
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    keys = ["page","guarani","espanhol","left_context","right_context","rule","confidence"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    print(f"[INFO] Saved {len(rows)} rows -> {out_csv}", file=sys.stderr)

def dedup_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in sorted(rows, key=lambda x: (-x.get("confidence",0), x.get("page", 10**9))):
        key = (r.get("guarani","").lower().strip(), r.get("espanhol","").lower().strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, help="Path to 1724.pdf")
    ap.add_argument("--outdir", default="out_restivo", help="Output directory")
    ap.add_argument("--min_conf", type=int, default=4, help="Minimum confidence for HIGH subset")
    ap.add_argument("--ctx", type=int, default=140, help="Context chars left/right")
    args = ap.parse_args()

    full_text, page_offsets = read_pdf_text(args.pdf)
    if not full_text:
        print("[FATAL] Empty text.", file=sys.stderr)
        sys.exit(2)

    # Normalize: reduce excessive whitespace
    import re as _re
    full_text = _re.sub(r"[ \\t]+", " ", full_text)
    pairs = mine_pairs(full_text, page_offsets, ctx_chars=args.ctx)
    print(f"[INFO] Total raw pairs mined: {len(pairs)}", file=sys.stderr)

    # Save raw all
    save_csv(pairs, os.path.join(args.outdir, "pairs_all_raw.csv"))

    # Dedup
    dedup = dedup_rows(pairs)
    save_csv(dedup, os.path.join(args.outdir, "pairs_dedup.csv"))

    # High-confidence subset
    min_conf = int(args.min_conf)
    high = [r for r in dedup if int(r.get("confidence",0)) >= min_conf]
    save_csv(high, os.path.join(args.outdir, f"pairs_high_conf_ge{min_conf}.csv"))

    # Quick stats
    stats = {
        "raw": len(pairs),
        "dedup": len(dedup),
        "high_conf(>=%d)" % min_conf: len(high)
    }
    with open(os.path.join(args.outdir, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Stats: {stats}", file=sys.stderr)

if __name__ == "__main__":
    main()
