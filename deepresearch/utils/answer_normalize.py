# deepresearch/utils/answer_normalize.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Set


_LEGAL_SUFFIXES = {
    "ltd",
    "limited",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "group",
    "gmbh",
    "sa",
    "spa",
    "plc",
    "llc",
    "srl",
    "bv",
    "ag",
    "kg",
    "oy",
    "oyj",
    "editore",
    "editori",
    "edizioni",
    "editrice",
    "press",
    "publishing",
    "publisher",
    "publishers",
}


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def _clean(text: str) -> str:
    t = _strip_accents(text).lower()
    t = re.sub(r"[\u2122\u00ae\u00a9]", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_answer(text: str) -> str:
    if not text:
        return ""
    tokens = []
    for tok in _clean(text).split():
        if not tok:
            continue
        if tok in _LEGAL_SUFFIXES:
            continue
        # Drop single-letter legal residue (e.g., "s p a" from "S.p.A.")
        if len(tok) == 1 and tok.isalpha():
            continue
        tokens.append(tok)
    return " ".join(tokens).strip()


def token_set(text: str) -> Set[str]:
    return set(normalize_answer(text).split())


def equivalent(a: str, b: str) -> bool:
    na = normalize_answer(a)
    nb = normalize_answer(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    ta = token_set(na)
    tb = token_set(nb)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.6


def extract_final_answer(text: str) -> str:
    if not text:
        return ""
    for line in text.splitlines():
        if line.lower().startswith("final answer:"):
            return line.split(":", 1)[-1].strip()
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _is_unknown(ans: str) -> bool:
    n = normalize_answer(ans)
    return n in {"unknown", "unk", "na", "n a"} or ans.strip() in {"?", ""}


def canonicalize_answer(raw_answer: str, candidates: Iterable[str]) -> str:
    # Keep unknown as-is to avoid forcing a wrong candidate.
    if _is_unknown(raw_answer):
        return raw_answer.strip()

    cand_list = [c.strip() for c in candidates if isinstance(c, str) and c.strip()]
    if not cand_list:
        return raw_answer.strip()

    matched = [c for c in cand_list if equivalent(raw_answer, c)]
    if not matched:
        return raw_answer.strip()

    # Prefer the most explicit form among equivalent candidates.
    matched.sort(key=lambda c: (len(token_set(c)), len(c)), reverse=True)
    return matched[0]
