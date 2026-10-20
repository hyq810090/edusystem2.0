"""Options per row of a ``choices`` environment.

A teacher fixes the layout with exam-zh's own key, ``\\begin{choices}[columns=N]``;
PDF (exam-zh), the web preview and Word all honour it. Without it exam-zh
measures the widest option for PDF, the web preview measures the rendered
options, and Word estimates the widest option here with the same rule: as many
columns as fit, halving from four (4, 2 or 1).
"""

import re

IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
OPTIONS_RE = re.compile(r"\\begin\{choices\}\s*\[([^\]\n]*)\]")
COLUMNS_RE = re.compile(r"(?:^|,)\s*columns\s*=\s*(\d+)\s*(?=,|$)")
MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)", re.S)

# Width exam-zh gives the options on A4 at the 10.5 pt body size (text block less
# the question-number indent, measured on exported PDFs), so Word decides as PDF does.
LINE_EM = 41.0
LABEL_EM = 1.7  # "A." and its separation
COLUMN_SEP_EM = 1.0  # exam-zh column-sep
IMAGE_EM = 7.0  # an option figure is sized to its cell; about the 3 cm of a PDF image option

FRACTIONS = {"frac", "dfrac", "tfrac", "cfrac"}
TEXT_GROUPS = {"mathrm", "text", "textrm", "mathbf", "mathit", "boldsymbol", "operatorname", "mathsf",
               "vec", "overrightarrow", "overline", "bar", "hat", "widehat", "tilde", "dot", "underline"}
RELATIONS = {"cdot", "times", "div", "pm", "mp", "le", "leq", "ge", "geq", "ne", "neq", "approx", "to",
             "rightarrow", "Rightarrow", "leftarrow", "Leftrightarrow", "in", "notin", "subset", "subseteq",
             "cup", "cap", "parallel", "perp", "sim", "cong", "equiv", "propto"}
SPACES = {"quad": 1.0, "qquad": 2.0, ",": 0.17, ":": 0.22, ";": 0.28, "!": -0.17, " ": 0.3}
SILENT = {"left", "right", "displaystyle", "textstyle", "limits", "big", "Big", "bigl", "bigr"}


def authored_columns(content: str) -> int | None:
    """``N`` of the first ``choices[columns=N]`` (1–4), or None for automatic."""
    options = OPTIONS_RE.search(content or "")
    found = options and COLUMNS_RE.search(options.group(1))
    columns = int(found.group(1)) if found else 0
    return columns if 1 <= columns <= 4 else None


def estimate_columns(choices: list[str]) -> int:
    """exam-zh's rule on estimated widths: fitting columns, halving from four."""
    if not choices:
        return 1
    widest = max(option_em(choice) for choice in choices) + LABEL_EM
    fit = int((LINE_EM + COLUMN_SEP_EM) // (widest + COLUMN_SEP_EM))
    columns = 4
    while columns > max(fit, 1):
        columns //= 2
    return columns


def option_em(text: str) -> float:
    """Approximate printed width of one option in em of the body font."""
    text = str(text or "")
    width = IMAGE_EM * len(IMAGE_RE.findall(text))
    text = IMAGE_RE.sub("", text)
    position = 0
    for match in MATH_RE.finditer(text):
        width += _text_em(text[position:match.start()])
        width += _math_em(next(group for group in match.groups() if group is not None))
        position = match.end()
    rest = text[position:]
    # Bare TeX in an option is typeset as math, as the preview does.
    return width + (_math_em(rest) if "\\" in rest else _text_em(rest))


def _char_em(char: str, math: bool) -> float:
    if ord(char) >= 0x2E80:
        return 1.0
    if char.isspace():
        return 0.0 if math else 0.3
    if char.isdigit():
        return 0.5
    if char.isalpha():
        return 0.55
    if math and char in "+-=<>":
        return 0.8
    return 0.35


def _text_em(text: str) -> float:
    return sum(_char_em(char, False) for char in text)


def _group(text: str, start: int) -> tuple[str, int]:
    """The braced group (or single token) at ``start`` and the index after it."""
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        return "", start
    if text[start] == "\\":
        command = re.match(r"\\([A-Za-z]+|.)", text[start:])
        return command.group(0), start + len(command.group(0))
    if text[start] != "{":
        return text[start], start + 1
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{" and text[index - 1:index] != "\\":
            depth += 1
        elif text[index] == "}" and text[index - 1:index] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return text[start + 1:], len(text)


def _math_em(text: str) -> float:
    width, index = 0.0, 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            command = re.match(r"\\([A-Za-z]+|.)", text[index:])
            name, index = command.group(1), index + len(command.group(0))
            if name in FRACTIONS:
                numerator, index = _group(text, index)
                denominator, index = _group(text, index)
                width += max(_math_em(numerator), _math_em(denominator)) + 0.2
            elif name == "sqrt":
                if text[index:index + 1] == "[":
                    index = text.find("]", index) + 1 or len(text)
                radicand, index = _group(text, index)
                width += _math_em(radicand) + 0.9
            elif name in TEXT_GROUPS:
                body, index = _group(text, index)
                width += _math_em(body)
            elif name in SPACES:
                width += SPACES[name]
            elif name in RELATIONS:
                width += 0.8
            elif name not in SILENT:
                width += 0.6
        elif char in "^_":
            script, index = _group(text, index + 1)
            width += 0.7 * _math_em(script)
        elif char in "{}":
            index += 1
        else:
            width += _char_em(char, True)
            index += 1
    return width
