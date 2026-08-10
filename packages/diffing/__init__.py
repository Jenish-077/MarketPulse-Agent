"""Snapshot diffing + noise filters."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from packages.policy import DEFAULT_NOISE_PATTERNS


@dataclass
class DiffResult:
    changed: bool
    change_ratio: float
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    changed_sections: list[str] = field(default_factory=list)
    is_noise_only: bool = False
    noise_reasons: list[str] = field(default_factory=list)


def _is_noise_line(line: str, extra_patterns: list[str] | None = None) -> bool:
    patterns = list(DEFAULT_NOISE_PATTERNS) + (extra_patterns or [])
    low = line.lower().strip()
    if not low or len(low) < 3:
        return True
    # Date-only lines
    if re.fullmatch(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2},? \d{4}|last updated:?.*)", low):
        return True
    for pat in patterns:
        if re.search(pat, low, re.I):
            return True
    return False


def cheap_text_diff(
    old_text: str,
    new_text: str,
    extra_noise_patterns: list[str] | None = None,
) -> DiffResult:
    if not old_text and new_text:
        return DiffResult(
            changed=True,
            change_ratio=1.0,
            added_lines=new_text.splitlines()[:80],
            changed_sections=[new_text[:4000]],
        )

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    ratio = sm.ratio()
    change_ratio = 1.0 - ratio

    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            added.extend(new_lines[j1:j2])
        elif tag == "delete":
            removed.extend(old_lines[i1:i2])
        elif tag == "replace":
            removed.extend(old_lines[i1:i2])
            added.extend(new_lines[j1:j2])

    signal_added = [l for l in added if not _is_noise_line(l, extra_noise_patterns)]
    signal_removed = [l for l in removed if not _is_noise_line(l, extra_noise_patterns)]

    noise_reasons = []
    if added and not signal_added and removed and not signal_removed:
        noise_reasons.append("all changed lines matched noise filters")
    elif change_ratio < 0.01:
        noise_reasons.append("change_ratio below 1%")

    # Build changed section windows for LLM extraction
    sections: list[str] = []
    if signal_added or signal_removed:
        chunk = []
        if signal_removed:
            chunk.append("REMOVED:\n" + "\n".join(signal_removed[:40]))
        if signal_added:
            chunk.append("ADDED:\n" + "\n".join(signal_added[:40]))
        sections.append("\n\n".join(chunk)[:6000])

    is_noise = bool(noise_reasons) or (change_ratio > 0 and not signal_added and not signal_removed)
    changed = change_ratio > 0.005 and not is_noise

    return DiffResult(
        changed=changed,
        change_ratio=round(change_ratio, 4),
        added_lines=signal_added[:100],
        removed_lines=signal_removed[:100],
        changed_sections=sections,
        is_noise_only=is_noise and change_ratio > 0,
        noise_reasons=noise_reasons,
    )
