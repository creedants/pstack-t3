import json
import posixpath
from dataclasses import dataclass
from enum import Enum
from brigade_lib import seam
from brigade_lib.shared import BrigadeError


class Kind(Enum):
    COPY = "copy"
    INDEX = "index"


@dataclass(frozen=True)
class Pattern:
    segments: tuple


@dataclass(frozen=True)
class Rule:
    source: Pattern
    target: Pattern
    kind: Kind
    skip_doc: bool = False


@dataclass(frozen=True)
class GeneratedMap:
    rules: tuple


EMPTY = GeneratedMap(())


def _norm_text(text):
    cleaned = text.strip().replace("\\", "/").strip("/")
    if not cleaned:
        return None
    if cleaned.startswith("/"):
        return None
    return cleaned


def _parse_pattern(text):
    made = _norm_text(text)
    if not made:
        return None
    parts = tuple(made.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        return None
    if any("*" in part and part not in ("*", "**") for part in parts):
        return None
    if "**" in parts[:-1]:
        return None
    return Pattern(parts)


def _captures_of(pattern):
    return sum(1 for part in pattern.segments if part in ("*", "**"))


def parse(rows):
    """GeneratedMap from contract rows. Malformed rows give EMPTY."""
    try:
        if not isinstance(rows, (list, tuple)):
            return EMPTY
        rules = []
        for row in rows:
            skip_doc = False
            if isinstance(row, dict):
                src_text = row.get("src")
                dst_text = row.get("dst")
                kind_text = row.get("kind")
                raw_skip = row.get("skip_doc", row.get("skip", False))
                if isinstance(raw_skip, bool):
                    skip_doc = raw_skip
                elif raw_skip in (0, 1):
                    skip_doc = bool(raw_skip)
                else:
                    if raw_skip not in (False, None):
                        return EMPTY
            elif isinstance(row, (list, tuple)) and len(row) == 3:
                src_text, dst_text, kind_text = row
            elif isinstance(row, (list, tuple)) and len(row) == 4:
                src_text, dst_text, kind_text, raw_skip = row
                if isinstance(raw_skip, bool):
                    skip_doc = raw_skip
                elif raw_skip in (0, 1):
                    skip_doc = bool(raw_skip)
                else:
                    return EMPTY
            else:
                return EMPTY
            if not isinstance(src_text, str) or not isinstance(dst_text, str):
                return EMPTY
            if kind_text not in ("copy", "index", Kind.COPY, Kind.INDEX):
                return EMPTY
            kind = Kind.COPY if kind_text in ("copy", Kind.COPY) else Kind.INDEX
            src_pat = _parse_pattern(src_text)
            dst_pat = _parse_pattern(dst_text)
            if src_pat is None or dst_pat is None:
                return EMPTY
            if kind == Kind.INDEX:
                if _captures_of(dst_pat) != 0:
                    return EMPTY
            else:
                if _captures_of(src_pat) != _captures_of(dst_pat):
                    return EMPTY
            rules.append(Rule(src_pat, dst_pat, kind, skip_doc))
        return GeneratedMap(tuple(rules))
    except Exception:
        return EMPTY


def read(project_root):
    """One status json call as a GeneratedMap. No contract or failed call gives EMPTY."""
    try:
        result = seam.land_result(project_root, "status", "--json")
        if result.returncode != 0 or not result.stdout.strip():
            return EMPTY
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            return EMPTY
        generated = payload.get("generated")
        if not generated:
            return EMPTY
        raw_map = payload.get("map")
        if not isinstance(raw_map, (list, tuple)) or not raw_map:
            raise BrigadeError(
                "contract lists generated paths but holds no generated map; "
                "run land.py init --generated-map <MAP> to install one")
        parsed = parse(raw_map)
        if not parsed.rules:
            raise BrigadeError(
                "contract lists generated paths but holds no generated map; "
                "run land.py init --generated-map <MAP> to install one")
        return parsed
    except BrigadeError:
        raise
    except Exception:
        return EMPTY


def _match(src, parts):
    segs = src.segments
    if segs and segs[-1] == "**":
        prefix = segs[:-1]
        if len(parts) < len(prefix):
            return None
        captures = []
        for want, got in zip(prefix, parts):
            if want == "*":
                captures.append(got)
            elif want != got:
                return None
        rest = parts[len(prefix):]
        captures.append("/".join(rest))
        return captures
    if len(parts) != len(segs):
        return None
    captures = []
    for want, got in zip(segs, parts):
        if want == "*":
            captures.append(got)
        elif want != got:
            return None
    return captures


def _substitute(dst, captures):
    segs = dst.segments
    if segs and segs[-1] == "**":
        base = segs[:-1]
        rest = captures[-1] if captures else ""
        head_captures = captures[:-1] if captures else []
        found = []
        index = 0
        for part in base:
            if part == "*":
                if index >= len(head_captures):
                    return None
                found.append(head_captures[index])
                index += 1
            else:
                found.append(part)
        if rest:
            return "/".join(found + [rest]) if found else rest
        return "/".join(found)
    found = []
    index = 0
    for part in segs:
        if part == "*":
            if index >= len(captures):
                return None
            found.append(captures[index])
            index += 1
        else:
            found.append(part)
    return "/".join(found)


def _join(pattern):
    return "/".join(pattern.segments)


def expand(gen_map, norm, doc_item=False):
    """Forward paths from one norm. Rules that skip doc items are skipped for doc items. A literal index rule also fires for any nonempty ancestor scope of its source. A literal copy rule also fires for its nonempty source parent scope. Rules with only single star wildcards do not match ancestor scopes."""
    try:
        if gen_map is None or not gen_map.rules:
            return ()
        if not norm:
            return ()
        parts = tuple(norm.split("/"))
        if any(part in ("", ".", "..") for part in parts):
            return ()
        found = []
        seen = set()
        for rule in gen_map.rules:
            if doc_item and rule.skip_doc:
                continue
            if _captures_of(rule.source) == 0 and _captures_of(rule.target) == 0:
                src_text = _join(rule.source)
                dst_text = _join(rule.target)
                if norm == src_text:
                    if dst_text not in seen:
                        seen.add(dst_text)
                        found.append(dst_text)
                    continue
                if rule.kind == Kind.COPY:
                    if posixpath.dirname(src_text) == norm:
                        if dst_text not in seen:
                            seen.add(dst_text)
                            found.append(dst_text)
                    continue
                if norm and src_text.startswith(norm + "/"):
                    if dst_text not in seen:
                        seen.add(dst_text)
                        found.append(dst_text)
                continue
            captures = _match(rule.source, parts)
            if captures is None:
                if rule.kind == Kind.INDEX:
                    continue
                src_text = _join(rule.source)
                if rule.source.segments and rule.source.segments[-1] == "**":
                    base = "/".join(rule.source.segments[:-1])
                    if norm == base:
                        dst_text = _substitute(rule.target, [""] if _captures_of(rule.source) == 1 else [])
                        if dst_text and dst_text not in seen:
                            seen.add(dst_text)
                            found.append(dst_text)
                continue
            if rule.kind == Kind.INDEX:
                dst_text = _join(rule.target)
                if dst_text not in seen:
                    seen.add(dst_text)
                    found.append(dst_text)
                continue
            dst_text = _substitute(rule.target, captures)
            if dst_text and dst_text not in seen:
                seen.add(dst_text)
                found.append(dst_text)
        return tuple(found)
    except Exception:
        return ()


def sources_of(gen_map, dest):
    """Source paths that copy to dest."""
    try:
        if gen_map is None or not gen_map.rules:
            return ()
        if not dest:
            return ()
        parts = tuple(dest.split("/"))
        found = []
        seen = set()
        for rule in gen_map.rules:
            if rule.kind != Kind.COPY:
                continue
            if _captures_of(rule.source) == 0 and _captures_of(rule.target) == 0:
                if dest == _join(rule.target):
                    src_text = _join(rule.source)
                    if src_text not in seen:
                        seen.add(src_text)
                        found.append(src_text)
                continue
            captures = _match(rule.target, parts)
            if captures is None:
                if rule.target.segments and rule.target.segments[-1] == "**":
                    base = "/".join(rule.target.segments[:-1])
                    if dest == base:
                        src_text = _substitute(rule.source, [""] if _captures_of(rule.target) == 1 else [])
                        if src_text and src_text not in seen:
                            seen.add(src_text)
                            found.append(src_text)
                continue
            src_text = _substitute(rule.source, captures)
            if src_text and src_text not in seen:
                seen.add(src_text)
                found.append(src_text)
        return tuple(found)
    except Exception:
        return ()


def _forward_skills(gen_map, norm):
    try:
        found = []
        for dest in expand(gen_map, norm):
            if dest.startswith("skills/"):
                found.append(dest)
        return tuple(found)
    except Exception:
        return ()


def twins(gen_map, norm):
    """One twin class holds the path, its sources, and its skills copies."""
    try:
        if gen_map is None or not gen_map.rules:
            return (norm,)
        found = {norm}
        try:
            for dest in _forward_skills(gen_map, norm):
                found.add(dest)
        except Exception:
            pass
        sources = set()
        for src in sources_of(gen_map, norm):
            sources.add(src)
        for src in list(sources):
            found.add(src)
            try:
                for dest in _forward_skills(gen_map, src):
                    found.add(dest)
            except Exception:
                pass
        return tuple(sorted(found))
    except Exception:
        return (norm,)
