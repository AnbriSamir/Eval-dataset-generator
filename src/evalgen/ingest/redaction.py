"""Categorized secret/PII scrubbing at the ingestion boundary (ADR-0001 rule 3).

Everything that survives ingestion gets hashed, embedded, clustered, sent to the judge
(an external API) and exported — so there is exactly one moment when redaction can be
total: here, before a record exists. The sibling repo scrubs its own payloads before
persistence, but that is best-effort and not a contract we may rely on (different
pattern set, generic sources promise nothing, files may predate the scrub): ingest
assumes every input line is hostile.

The order inside :func:`sanitize_text` is load-bearing:

1. **Normalize first** — invisible characters (the whole Unicode ``Cf`` format
   category + known invisible stragglers, stripped on BOTH sides of NFKC — see
   :func:`normalize_text` for why both), NFKC folds fullwidth homoglyphs
   (``ｓｋ－`` → ``sk-``), CR/CRLF become ``\\n``. Regexes only see normalized text,
   so the unicode tricks that would walk past a raw-text scrub die here.
   Normalization also makes unicode-equivalent texts hash equal — a dedup
   correctness win, not just a security step.
2. **Scrub second** — compiled patterns in a fixed order, specific before generic, so
   categories stay precise (a JWT must not be half-eaten by the opaque-token rule).
3. **Constant placeholders** ``[REDACTED:<category>]`` — never a hash or salt of the
   secret (that would leak bits and break the same-id-regardless-of-secret invariant).
   Two records differing only in their secrets therefore become exact duplicates
   post-redaction and dedup collapses them — correct: they ARE the same eval case.

Honest limitation (stated, tested, not hidden): regex scrubbing reduces the blast
radius; it is not a guarantee (same stance as the sibling's ``memory/redaction.py``).
The chosen failure direction is OVER-redaction: 40-hex git SHAs and long base64 blobs
in legitimate content get eaten — accepted, and pinned by a test so the behavior is a
documented decision rather than a surprise.
"""

from __future__ import annotations

import functools
import re
import unicodedata

#: Invisible code points OUTSIDE the ``Cf`` category that still render as nothing and
#: can therefore split a token: U+034F COMBINING GRAPHEME JOINER (category Mn — and it
#: BLOCKS canonical composition, which is why stripping must run BEFORE NFKC),
#: variation selectors U+FE00–FE0F and U+E0100–E01EF (Mn), and the invisible Hangul
#: fillers U+115F/U+1160/U+3164/U+FFA0 (Lo letters that render as blank; NFKC folds
#: the two compatibility forms INTO U+1160, which is why stripping must ALSO run
#: AFTER NFKC).
_INVISIBLE_EXTRA: frozenset[int] = frozenset(
    {0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0}
    | set(range(0xFE00, 0xFE10))
    | set(range(0xE0100, 0xE01F0))
)


@functools.cache
def _is_invisible(char: str) -> bool:
    """True for every code point an adversary can hide inside a token.

    The whole ``Cf`` (format) category, not a hand-picked table: the red-team payload
    that killed the previous 4-char zero-width table used U+2060 WORD JOINER — the
    direct successor of U+FEFF, the one word joiner that table DID cover. Enumerating
    members of a family whose membership grows with Unicode revisions is a losing
    game; the category is the contract. ``Cf`` covers the zero-widths (U+200B–200D),
    soft hyphen (U+00AD), bidi marks (U+200E/F, U+061C), word joiner (U+2060),
    invisible operators (U+2061–2064), BOM (U+FEFF) and the tag block
    (U+E0000–E007F — the ASCII-smuggling alphabet). ``_INVISIBLE_EXTRA`` adds the
    invisible non-``Cf`` stragglers. Cached per code point: the predicate runs on
    every character of every ingested line.
    """
    return unicodedata.category(char) == "Cf" or ord(char) in _INVISIBLE_EXTRA


def _strip_invisibles(text: str) -> str:
    return "".join(char for char in text if not _is_invisible(char))


#: Ordered (category, pattern) pairs — order is part of the contract (ADR-0001 rule 3):
#: specific before generic so each secret gets its most precise category, and a broader
#: rule never chews a recognizable token into an unrecognizable half.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # JWTs: three base64url segments, header always starts with eyJ ('{"' encoded).
    # Must run before the opaque-token rule, which would eat single segments.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")),
    # Provider API keys (OpenAI sk-…, Anthropic sk-ant-…).
    ("api_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b")),
    # AWS access key ids.
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Bearer / Authorization headers ("Bearer xxx", "authorization: xxx", "bearer=xxx").
    ("bearer", re.compile(r"(?i)\b(?:bearer|authorization)\b\s*[:=]?\s*[A-Za-z0-9._-]{12,}")),
    # key[:=]value where the key name signals a secret. Optional quotes so stringified
    # JSON-in-JSON ('{"api_key": "…"}') is caught too. The negative lookahead keeps an
    # already-redacted value from being re-eaten, preserving its more precise category.
    (
        "secret_kv",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|access[_-]?key|credentials?)"
            r"\b[\"']?\s*[:=]\s*[\"']?(?!\[REDACTED:)\S+"
        ),
    ),
    # Long opaque tokens (>=32 base64/hex-ish chars): the common shape of a leaked
    # secret with no recognizable prefix. Also eats git SHAs — accepted over-redaction.
    ("token", re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}\b")),
    # Email addresses (PII).
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # Phone numbers: international +… and spaced/dotted French 0X XX XX XX XX forms
    # ONLY — deliberately conservative so bare numeric ids/counters are not eaten.
    ("phone", re.compile(r"\+\d{1,3}(?:[ .-]?\d){6,12}\b|\b0[1-9](?:[ .]\d{2}){4}\b")),
    # Local user paths: the username segment is PII (and an absolute path fingerprints
    # the machine). Windows C:\Users\<name> (either slash) and Unix /home|/Users/<name>.
    (
        "user_path",
        re.compile(r"(?i)\b[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+" r"|/(?:home|Users)/[^/\s\"']+"),
    ),
)


def normalize_text(text: str) -> str:
    """Invisible strip + NFKC + invisible strip + newline normalization + C0-control
    strip — ALWAYS before scrubbing.

    The strip runs on BOTH sides of NFKC, and each side is load-bearing (verified
    against unicodedata, pinned by tests — do not "simplify" to one pass):

    - **Before**, because U+034F CGJ has combining class 0 and BLOCKS canonical
      composition: ``e + CGJ + ́`` would otherwise stay decomposed through NFKC and
      hash-diverge from ``é`` after the CGJ is removed — a dedup miss, not just a
      redaction gap.
    - **After**, because NFKC itself can EMIT an invisible character (U+3164 and
      U+FFA0 both fold to U+1160 HANGUL JUNGSEONG FILLER) — a single pre-pass would
      let the fold re-introduce a token splitter.

    Then CRLF/CR become ``\\n`` so the same logical text hashes identically across
    platforms.
    """
    folded = unicodedata.normalize("NFKC", _strip_invisibles(text))
    newline_normal = _strip_invisibles(folded).replace("\r\n", "\n").replace("\r", "\n")
    return _strip_controls(newline_normal)


def _strip_controls(text: str) -> str:
    """Remove C0 control characters (except ``\\n``/``\\t``) and DEL.

    Red-team MINOR: ``derive_record_id`` and ``canonical_text`` join parts with
    ``CANONICAL_SEP = "\\x1f"`` under the assumption it "cannot appear in normal
    text" — but ingest assumes HOSTILE input, and an injected 0x1f made two
    distinct exchanges collide (same id, same dedup key). Stripping the whole C0
    family here turns that assumption into an enforced invariant instead of an
    assertion. Runs AFTER newline normalization so ``\\r`` still folds to ``\\n``
    rather than being silently dropped.
    """
    return "".join(
        char for char in text if (ord(char) >= 0x20 and ord(char) != 0x7F) or char in "\n\t"
    )


def sanitize_text(text: str) -> str:
    """Normalize then scrub: the ONLY transformation raw text goes through before it
    may be hashed, embedded, stored, or shown to anything downstream.

    Idempotent by construction (placeholders match none of the patterns — pinned by a
    test), so defensive re-application at a lower layer never corrupts text.
    """
    out = normalize_text(text)
    for category, pattern in _PATTERNS:
        out = pattern.sub(f"[REDACTED:{category}]", out)
    return out


def scrub_value(value: object) -> object:
    """Sanitize every string reachable inside a structured value — dict KEYS included.

    Payload values are arbitrary JSON (dicts/lists/scalars), so scrubbing must chase
    every shape, not just top-level strings; a secret-bearing dict key would otherwise
    leak straight through (same precedent as the sibling's ``trace_store._scrub_value``).
    Non-string scalars (int/float/bool/None) carry no text and pass through unchanged.
    """
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            (sanitize_text(k) if isinstance(k, str) else k): scrub_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item) for item in value)
    return value
