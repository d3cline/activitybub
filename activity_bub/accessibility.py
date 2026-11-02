"""Saved visual preferences and plain, CW-aware descriptions for assistive tools."""
from dataclasses import asdict, dataclass, replace
import re


VISION_OPTIONS = (
    ("standard", "Use color theme"),
    ("tritan", "Blue / yellow friendly (tritan)"),
    ("protan", "Red-sensitive friendly (protan)"),
    ("deutan", "Green-sensitive friendly (deutan)"),
    ("mono", "Monochrome"),
    ("warm", "Warm colors · less blue"),
)
CONTRAST_OPTIONS = (("theme", "Use theme contrast"), ("dark", "High contrast · dark"),
                    ("light", "High contrast · light"))
FONT_OPTIONS = (("system", "System font"), ("sans", "Sans serif"),
                ("serif", "Serif"), ("mono", "Monospace"))
SCALES = (100, 125, 150, 175, 200)


@dataclass(frozen=True)
class Accessibility:
    vision: str = "standard"
    contrast: str = "theme"
    scale: int = 100
    font: str = "system"
    bold: bool = False
    large_controls: bool = False
    hide_images: bool = False
    reduce_motion: bool = False
    screen_reader: bool = False
    image_filters: bool = True

    @classmethod
    def load(cls, value):
        value = value if isinstance(value, dict) else {}
        options = {"vision": VISION_OPTIONS, "contrast": CONTRAST_OPTIONS, "font": FONT_OPTIONS}
        defaults = cls()
        fields = {}
        for name in cls.__dataclass_fields__:
            v = value.get(name, getattr(defaults, name))
            if name in options:
                fields[name] = v if isinstance(v, str) and v in dict(options[name]) else getattr(defaults, name)
            elif name == "scale":
                fields[name] = v if type(v) is int and v in SCALES else 100
            else:
                fields[name] = v if type(v) is bool else getattr(defaults, name)
        return cls(**fields)

    def saved(self):
        return asdict(self)

    @property
    def motion_off(self):
        return self.reduce_motion or self.screen_reader

    @property
    def image_signature(self):
        return (self.vision, self.contrast) if self.image_filters else ("standard", "theme")


def adapt_theme(theme, prefs):
    # Interface palettes. Preview pixel transforms live in image_visibility.py.
    # Labels, check marks and focus outlines also carry meaning without color.
    accents = {"tritan": ("#ff9eaa", "#25090f"), "protan": ("#8bcaff", "#071723"),
               "deutan": ("#ffd37f", "#241800")}
    if prefs.vision in accents:
        accent, ink = accents[prefs.vision]
        theme = replace(theme, bg="#101116", panel="#191b23", card="#20232c", line="#85909c",
                        text="#ffffff", muted="#d4d8df", accent=accent, accent_text=ink,
                        selected="#333640", canvas="#111217", warning="#ffffff")
    elif prefs.vision == "mono":
        theme = replace(theme, bg="#111111", panel="#191919", card="#222222", line="#969696",
                        text="#ffffff", muted="#dddddd", accent="#ffffff", accent_text="#111111",
                        selected="#383838", canvas="#151515", warning="#ffffff")
    elif prefs.vision == "warm":
        theme = replace(theme, bg="#1b1008", panel="#28190b", card="#30200e", line="#a38b64",
                        text="#fff0c2", muted="#e7ce9b", accent="#ffd078", accent_text="#261500",
                        selected="#47331b", canvas="#1f1408", warning="#ffe2a4")
    if prefs.contrast == "dark":
        accent = theme.accent if prefs.vision != "standard" else "#ffff80"
        theme = replace(theme, bg="#000000", panel="#080808", card="#101010", line="#d0d0d0",
                        text="#ffffff", muted="#eeeeee", accent=accent, accent_text="#000000",
                        selected="#242424", canvas="#000000", warning="#ffffff")
    elif prefs.contrast == "light":
        theme = replace(theme, bg="#ffffff", panel="#fafafa", card="#ffffff", line="#333333",
                        text="#000000", muted="#222222", accent="#000000", accent_text="#ffffff",
                        selected="#e2e2e2", canvas="#ffffff", warning="#000000")
    return theme


def scale_css(css, scale):
    return re.sub(r"(font-size\s*:\s*)(\d+(?:\.\d+)?)px",
                  lambda m: f"{m[1]}{float(m[2])*scale/100:g}px", css)


def post_description(post, covered=False, compact=False):
    parts = [post.author, "@" + post.handle]
    if post.warning or post.sensitive:
        parts.append("Content warning: " + (post.warning or "Sensitive media"))
    if covered:
        parts.append("Content hidden. Press Enter, then Reveal this post to read.")
    else:
        body = post.text
        parts.append(body[:240] + ("… Open to read more." if len(body) > 240 else "") if compact else body)
        for i, media in enumerate(post.media, 1):
            alt = media.get("alt") or "No description provided."
            parts.append(f"Attachment {i}: " + (alt[:100] + ("…" if len(alt)>100 else "") if compact else alt))
    parts.append(f"{post.replies} replies, {post.boosts} boosts, {post.likes} likes.")
    if post.saved:
        parts.append("Saved.")
    parts.append("Server: " + post.server)
    return "\n".join(part for part in parts if part)
