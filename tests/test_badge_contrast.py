"""TR-2/TR-3/TR-4: provider and stream badges meet WCAG AA in both themes."""

import re
from pathlib import Path

TOKENS = Path("src/lookout/adapters/web/static/css/tokens.css")
CSS = Path("src/lookout/adapters/web/static/css/viewer.css")

BADGES = ("ring", "wyze", "generic", "live", "ondemand")
RULE_SELECTORS = (
    ".badge-provider.ring",
    ".badge-provider.wyze",
    ".badge-provider.generic",
    ".badge-stream.live_rtc",
    ".badge-stream.on_demand",
)


def _root_block(tokens: str) -> str:
    match = re.search(r"(?m)^:root\s*\{([^{}]*)\}", tokens)
    assert match, "missing :root token block"
    return match.group(1)


def _dark_block(tokens: str) -> str:
    match = re.search(
        r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^{}]*)\}\s*\}",
        tokens,
    )
    assert match, "missing dark-mode :root token block"
    return match.group(1)


def _token(block: str, name: str, slot: str) -> str:
    match = re.search(rf"--badge-{name}-{slot}:\s*(#[0-9a-fA-F]{{6}})\s*;", block)
    assert match, f"missing --badge-{name}-{slot} hex token"
    return match.group(1)


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    light, dark = _luminance(fg), _luminance(bg)
    return (max(light, dark) + 0.05) / (min(light, dark) + 0.05)


def _named_token(block: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", block)
    assert match, f"missing --{name} hex token"
    return match.group(1)


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^{{}}]*)\}}", css)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_light_theme_badge_pairs_meet_wcag_aa() -> None:
    block = _root_block(TOKENS.read_text(encoding="utf-8"))
    for name in BADGES:
        fg, bg = _token(block, name, "fg"), _token(block, name, "bg")
        ratio = _contrast(fg, bg)
        assert ratio >= 4.5, f"light {name}: {fg} on {bg} = {ratio:.2f}:1"


def test_dark_theme_badge_pairs_meet_wcag_aa() -> None:
    block = _dark_block(TOKENS.read_text(encoding="utf-8"))
    for name in BADGES:
        fg, bg = _token(block, name, "fg"), _token(block, name, "bg")
        ratio = _contrast(fg, bg)
        assert ratio >= 4.5, f"dark {name}: {fg} on {bg} = {ratio:.2f}:1"


def test_badge_rules_use_theme_tokens() -> None:
    css = CSS.read_text(encoding="utf-8")
    for selector in RULE_SELECTORS:
        body = _rule(css, selector)
        assert "var(--badge-" in body, selector
        for legacy in ("#38bdf8", "#34d399", "rgba(0, 195, 137"):
            assert legacy not in body, f"{selector} still uses {legacy}"


def test_event_time_aa_token_meets_wcag_aa_in_both_themes() -> None:
    """The dedicated --text-muted-aa pair clears AA on --bg-subtle in both themes."""
    tokens = TOKENS.read_text(encoding="utf-8")
    for theme, block in (("light", _root_block(tokens)), ("dark", _dark_block(tokens))):
        fg = _named_token(block, "text-muted-aa")
        bg = _named_token(block, "bg-subtle")
        ratio = _contrast(fg, bg)
        assert ratio >= 4.5, f"{theme}: {fg} on {bg} = {ratio:.2f}:1"


def test_event_time_rule_uses_aa_token_not_legacy_muted() -> None:
    body = _rule(CSS.read_text(encoding="utf-8"), ".event-time")
    assert "var(--text-muted-aa)" in body, ".event-time must use --text-muted-aa"
    assert not re.search(r"var\(--text-muted\)", body), (
        ".event-time must not fall back to legacy --text-muted"
    )
