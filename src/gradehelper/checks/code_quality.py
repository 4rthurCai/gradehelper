"""Static code-quality heuristics for MATLAB, C and C++ submissions.

Ported unchanged from the legacy util.passCodeQuality so scores stay identical.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

C_FAMILY_SUFFIXES = (".c", ".cc", ".cpp", ".hpp", ".h", ".cxx")
MATLAB_SUFFIXES = (".m",)

GLOBAL_VARS = "Usage of global variables"
WHILE_TRUE = "Usage of 'while true'"
ELSEIF_NO_ELSE = "if-elseif structure without final else"
SWITCH_NO_OTHERWISE = "switch structure without otherwise statement"
SPRINTF_DISP = "use sprintf() and then disp() instead of directly using fprintf()"
NON_CONST_VARS = "Non-const variables found"
USING_NAMESPACE = "Usage of 'using namespace'"


class CtagsMissing(RuntimeError):
    pass


def applies_to(file_name: str, language: str) -> bool:
    if language == "matlab":
        return file_name.endswith(MATLAB_SUFFIXES)
    return file_name.endswith(C_FAMILY_SUFFIXES)


def check_file(path: Path, language: str) -> list[str]:
    """Return the distinct issue descriptions found in one file."""
    if language == "matlab":
        return check_matlab(path.read_text(encoding="utf-8", errors="replace"))
    if language == "c":
        return [NON_CONST_VARS] if _non_const_globals(path) else []
    if language == "cc":
        issues = []
        content = _strip_c_comments_and_strings(path.read_text(encoding="utf-8", errors="replace"))
        if re.search(r"\busing\s+namespace\s+", content):
            issues.append(USING_NAMESPACE)
        if _non_const_globals(path):
            issues.append(NON_CONST_VARS)
        return issues
    return []


def check_matlab(content: str) -> list[str]:
    issues = []
    if "global " in content:
        issues.append(GLOBAL_VARS)
    if "while true" in content:
        issues.append(WHILE_TRUE)
    cleaned = _strip_matlab_comments_and_strings(content)
    if not _every_elseif_has_else(cleaned):
        issues.append(ELSEIF_NO_ELSE)
    if not _every_switch_has_otherwise(cleaned):
        issues.append(SWITCH_NO_OTHERWISE)
    if _has_sprintf_then_disp(cleaned):
        issues.append(SPRINTF_DISP)
    return issues


def _non_const_globals(path: Path) -> bool:
    """ctags lists file-scope variables; any without 'const' is an issue."""
    if shutil.which("ctags") is None:
        raise CtagsMissing("ctags not found; install universal-ctags (brew install universal-ctags)")
    out = subprocess.check_output(["ctags", "-x", "--sort=yes", str(path)])
    for line in out.splitlines():
        fields = line.split(None, 4)
        if len(fields) >= 4 and fields[1] == b"variable" and b"const" not in line:
            return True
    return False


def _strip_c_comments_and_strings(content: str) -> str:
    content = re.sub(r"/\*.*?\*/", " ", content, flags=re.DOTALL)
    lines = []
    for line in content.split("\n"):
        out, quote, i = [], None, 0
        while i < len(line):
            ch = line[i]
            if quote is None:
                if ch in ('"', "'"):
                    quote = ch
                    out.append(" ")
                elif line.startswith("//", i):
                    break
                else:
                    out.append(ch)
            elif ch == "\\" and i + 1 < len(line):
                out.append("  ")
                i += 1
            else:
                if ch == quote:
                    quote = None
                out.append(" ")
            i += 1
        lines.append("".join(out))
    return "\n".join(lines)


def _strip_matlab_comments_and_strings(content: str) -> str:
    lines = []
    for line in content.split("\n"):
        out, quote = [], None
        for i, ch in enumerate(line):
            if quote is None:
                if ch in ("'", '"'):
                    quote = ch
                    out.append(" ")
                elif ch == "%":
                    break
                else:
                    out.append(ch)
            else:
                if ch == quote:
                    if i + 1 < len(line) and line[i + 1] == quote:
                        out.append(" ")
                        continue
                    quote = None
                out.append(" ")
        lines.append("".join(out))
    return "\n".join(lines)


_BLOCK_START = re.compile(r"\b(if|for|while|switch|function|parfor|spmd|try)\b")
_STATEMENT_END = re.compile(r"(^|[\s;])\s*end\s*($|[;\s]|%)")


def _every_elseif_has_else(content: str) -> bool:
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        if re.search(r"\bif\b", lines[i].strip()):
            has_elseif = has_else = False
            depth = 1
            i += 1
            while i < len(lines) and depth > 0:
                inner = lines[i].strip()
                if _BLOCK_START.search(inner):
                    depth += 1
                elif _STATEMENT_END.search(inner):
                    depth -= 1
                    if depth == 0:
                        break
                if depth == 1:
                    if re.search(r"\belseif\b", inner):
                        has_elseif = True
                    elif re.search(r"\belse\b", inner):
                        has_else = True
                i += 1
            if has_elseif and not has_else:
                return False
        i += 1
    return True


def _every_switch_has_otherwise(content: str) -> bool:
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        if re.search(r"\bswitch\b", lines[i].strip()):
            has_otherwise = False
            depth = 1
            i += 1
            while i < len(lines) and depth > 0:
                inner = lines[i].strip()
                if re.search(r"\bswitch\b", inner):
                    depth += 1
                elif re.search(r"\bend\b", inner):
                    depth -= 1
                    if depth == 0:
                        break
                if depth == 1 and re.search(r"\botherwise\b", inner):
                    has_otherwise = True
                i += 1
            if not has_otherwise:
                return False
        i += 1
    return True


def _has_sprintf_then_disp(content: str) -> bool:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        match = re.search(r"(\w+)\s*=\s*sprintf\s*\(", line.strip())
        if not match:
            continue
        disp = re.compile(rf"\bdisp\s*\(\s*{re.escape(match.group(1))}\s*\)")
        if any(disp.search(lines[j].strip()) for j in range(i + 1, min(i + 5, len(lines)))):
            return True
    return False
