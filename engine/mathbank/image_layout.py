"""Image anchors and per-image presentation, shared by persistence and exports."""

import json
import re

IMAGE_RE = re.compile(r"!\[.*?\]\(([^)]+)\)")
ENV_RE = re.compile(r"\\(begin|end)\{(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr|choices)\}")
SIZE_CM = {"small": (5.0, 4.2), "medium": (8.0, 6.0), "large": (10.5, 7.5)}


def has_figure_choice_options(content: str, question_type: str) -> bool:
    """Only recognized options on a choice question qualify for side columns."""
    if question_type not in ("single_choice", "multi_choice"):
        return False
    source = str(content or "")
    block = re.search(r"\\begin\{choices\}(?:\[[^\]]*\])?([\s\S]*?)\\end\{choices\}", source)
    if block:
        return any(item.strip() for item in re.split(r"\\item\b", block[1])[1:])
    return bool(re.search(r"(?:^|\n)\s*[-*]?\s*A[.、．]\s*\S", source)
                and re.search(r"\n\s*[-*]?\s*B[.、．]\s*\S", source))


def image_key(path: str) -> str:
    return re.sub(r"^/?(?:static/)?uploads/", "", str(path or "").strip())


def split_image_anchors(content: str) -> tuple[str, str]:
    """Detach only the final image cluster outside a table/choices environment."""
    source = str(content or "")
    end = len(source)
    for match in reversed(list(IMAGE_RE.finditer(source))):
        if source[match.end():end].strip():
            break
        stack = []
        for env in ENV_RE.finditer(source[:match.start()]):
            if env[1] == "begin":
                stack.append(env[2])
            elif stack and stack[-1] == env[2]:
                stack.pop()
        if stack:
            break
        end = match.start()
    return source[:end].rstrip(), source[end:].strip()


def split_figure_anchors(content: str) -> tuple[str, str]:
    """Include a standalone figure cluster immediately before the choices.

    Match the browser's forFigureLayout without modifying stored content.
    Tables, choice images and interleaved/captioned figures keep their anchors.
    """
    source = str(content or "")
    choice = re.search(r"\\begin\{choices\}|^\s*[-*]?\s*A[.、．]\s+", source, re.M)
    if choice:
        stem, figures = split_image_anchors(source[:choice.start()])
        if figures and re.fullmatch(r"(?:\s*!\[.*?\]\([^)]+\)\s*)+", figures):
            start = source[:choice.start()].rfind(figures)
            line_start = source.rfind("\n", 0, start) + 1
            if not source[line_start:start].strip():
                rest, tail = split_image_anchors(source[choice.start():].strip())
                source = stem + "\n" + rest + "\n" + figures + ("\n" + tail if tail else "")
    return split_image_anchors(source)


def normalize_image_layouts(value, content: str) -> dict:
    """Keep only visible image references and enum values; never accept CSS/TeX."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("插图独立排版格式无效") from exc
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("插图独立排版必须为最多 200 项的对象")
    keys = {image_key(m[1]) for m in IMAGE_RE.finditer(content or "")}
    result = {}
    for path, layout in value.items():
        key = image_key(path)
        if not isinstance(layout, dict):
            raise ValueError("插图独立排版格式无效")
        align, size = layout.get("align", "center"), layout.get("size", "auto")
        if align not in ("left", "center", "right") or size not in ("auto", *SIZE_CM):
            raise ValueError("无效的插图位置或尺寸")
        if key in keys:
            result[key] = {"align": align, "size": size}
    return result
