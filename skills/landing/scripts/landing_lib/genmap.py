import posixpath
from landing_lib import seam


def _split_pattern(text):
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if any("*" in part and part not in ("*", "**") for part in parts):
        return None
    return tuple(parts)


def _captures(pattern):
    return sum(1 for part in pattern if part in ("*", "**"))


def _norm_item(text):
    cleaned = text.strip().replace("\\", "/").strip("/")
    if not cleaned:
        return None
    if cleaned.startswith("/"):
        return None
    return cleaned


def _inside_roots(target, roots):
    for root in roots:
        cleaned = root.strip().replace("\\", "/").strip("/")
        if not cleaned:
            continue
        if target == cleaned:
            return True
        if target.startswith(cleaned + "/"):
            return True
    return False


def _literal_prefix(pattern):
    found = []
    for part in pattern:
        if part in ("*", "**"):
            break
        found.append(part)
    return "/".join(found)


def map_rows(pairs, roots):
    """Contract rows from parsed pairs. Refuses bad targets and bad captures."""
    rows = []
    cleaned_roots = []
    for root in roots or []:
        made = _norm_item(root)
        if made:
            cleaned_roots.append(made)
    for src_text, dst_text, kind in pairs:
        src_made = _norm_item(src_text)
        dst_made = _norm_item(dst_text)
        if not src_made or not dst_made:
            raise seam.LandError("generated map needs repo relative src and dst with no dot parts")
        src_pat = _split_pattern(src_made)
        dst_pat = _split_pattern(dst_made)
        if src_pat is None or dst_pat is None:
            raise seam.LandError("generated map refuses dot parts and inline stars")
        if "**" in src_pat[:-1] or "**" in dst_pat[:-1]:
            raise seam.LandError("generated map allows double star only as the last part")
        if kind == "index":
            if _captures(dst_pat) != 0:
                raise seam.LandError("generated map index target must be literal")
        elif kind == "copy":
            if _captures(src_pat) != _captures(dst_pat):
                raise seam.LandError("generated map copy needs the same star count in src and dst")
        else:
            raise seam.LandError("generated map kind must be copy or index")
        if kind == "copy" and _captures(dst_pat) > 0:
            base = _literal_prefix(dst_pat)
            if not base or not _inside_roots(base, cleaned_roots):
                raise seam.LandError("generated map target sits outside the generated roots")
        elif not _inside_roots(dst_made, cleaned_roots):
            raise seam.LandError("generated map target sits outside the generated roots")
        rows.append([src_made, dst_made, kind])
    return rows


def parse_map(passed, roots):
    """Rows for the contract from a comma string. None keeps the stored map."""
    if passed is None:
        return None
    text = passed.strip()
    if not text:
        return []
    pairs = []
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            raise seam.LandError("generated map holds no empty item")
        if "=" not in item:
            raise seam.LandError("generated map item needs src equals dst")
        src_text, _, dst_text = item.partition("=")
        src_text = src_text.strip()
        dst_text = dst_text.strip()
        if not src_text or not dst_text:
            raise seam.LandError("generated map needs repo relative src and dst with no dot parts")
        kind = "copy"
        if dst_text.endswith("!"):
            kind = "index"
            dst_text = dst_text[:-1].strip()
            if not dst_text:
                raise seam.LandError("generated map needs repo relative src and dst with no dot parts")
        pairs.append((src_text, dst_text, kind))
    return map_rows(pairs, roots)
