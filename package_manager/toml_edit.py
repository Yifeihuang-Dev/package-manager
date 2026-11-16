"""Minimal text editing of a project's pkm.toml: only touch the [dependencies] block, leave everything else as-is."""

import re


def _section_bounds(lines, header):
    """Return (section line index, section end line index); None if the section is absent."""
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == header:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith("["):
            end = j
            break
    return start, end


def set_dependency(text: str, name: str, version: str) -> str:
    """Add/update one dependency in [dependencies], preserving the rest of the file."""
    lines = text.splitlines()
    newline = f'{name} = "{version}"'
    pat = re.compile(rf"^\s*{re.escape(name)}\s*=")

    bounds = _section_bounds(lines, "[dependencies]")
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[dependencies]")
        lines.append(newline)
        return "\n".join(lines).rstrip("\n") + "\n"

    s, e = bounds
    for i in range(s + 1, e):
        if pat.match(lines[i]):
            lines[i] = newline
            return "\n".join(lines).rstrip("\n") + "\n"
    lines.insert(e, newline)
    return "\n".join(lines).rstrip("\n") + "\n"


def remove_dependency(text: str, name: str) -> str:
    """Delete one line from [dependencies], preserving the rest of the file."""
    lines = text.splitlines()
    bounds = _section_bounds(lines, "[dependencies]")
    if bounds is None:
        return text
    s, e = bounds
    pat = re.compile(rf"^\s*{re.escape(name)}\s*=")
    out = lines[: s + 1] + [ln for ln in lines[s + 1 : e] if not pat.match(ln)] + lines[e:]
    return "\n".join(out).rstrip("\n") + "\n"
