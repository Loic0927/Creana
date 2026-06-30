import re
from html import escape
from html.parser import HTMLParser

from django.utils.safestring import mark_safe


ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "ol",
    "ul",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "span",
}
BLOCK_TAGS = {"p", "ol", "ul", "li", "h1", "h2", "h3", "h4", "h5"}
BLOCKED_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed"}
STYLE_TAGS = {"span", "p", "li", "h1", "h2", "h3", "h4", "h5"}
ALLOWED_TEXT_ALIGN = {"left", "right", "center", "justify"}
ALLOWED_FONT_STYLE = {"normal", "italic", "oblique"}
ALLOWED_FONT_WEIGHT = {"normal", "bold", "bolder", "lighter"}
ALLOWED_COLOR_NAMES = {
    "black",
    "blue",
    "gray",
    "green",
    "grey",
    "navy",
    "purple",
    "red",
    "teal",
    "white",
}
FONT_SIZE_RE = re.compile(r"^(?:1[0-9]|[2-4][0-9]|5[0-6])px$")
FONT_SIZE_PT_RE = re.compile(r"^(\d+(?:\.\d+)?)pt$")
COLOR_RE = re.compile(
    r"^(?:#[0-9a-f]{3}(?:[0-9a-f]{3})?|rgb\(\s*(?:\d{1,3}\s*,\s*){2}\d{1,3}\s*\)|rgba\(\s*(?:\d{1,3}\s*,\s*){3}(?:0|1|0?\.\d+)\s*\))$"
)


class ArticleHtmlSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in BLOCKED_CONTENT_TAGS:
            self.blocked_depth += 1
            return

        if self.blocked_depth:
            return

        if tag not in ALLOWED_TAGS:
            return

        if tag in STYLE_TAGS:
            style = self._clean_style(attrs)
            if style:
                self.parts.append(f'<{tag} style="{style}">')
                return
            self.parts.append(f"<{tag}>")
            return

        if tag == "br":
            self.parts.append("<br>")
            return

        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BLOCKED_CONTENT_TAGS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return

        if self.blocked_depth:
            return

        if tag in ALLOWED_TAGS and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.blocked_depth:
            return
        self.parts.append(escape(data))

    def handle_entityref(self, name):
        if self.blocked_depth:
            return
        self.parts.append(escape(f"&{name};"))

    def handle_charref(self, name):
        if self.blocked_depth:
            return
        self.parts.append(escape(f"&#{name};"))

    def _clean_style(self, attrs):
        attr_map = {name.lower(): value for name, value in attrs}
        declarations = []
        for declaration in attr_map.get("style", "").split(";"):
            if ":" not in declaration:
                continue
            property_name, value = [part.strip().lower() for part in declaration.split(":", 1)]
            if property_name == "font-size" and FONT_SIZE_RE.match(value):
                declarations.append(f"font-size: {value}")
            elif property_name == "font-size":
                point_match = FONT_SIZE_PT_RE.match(value)
                if point_match:
                    pixel_value = round(float(point_match.group(1)) * 1.333)
                    if 10 <= pixel_value <= 56:
                        declarations.append(f"font-size: {pixel_value}px")
            elif property_name == "font-weight" and (
                value in ALLOWED_FONT_WEIGHT or (value.isdigit() and 100 <= int(value) <= 900)
            ):
                declarations.append(f"font-weight: {value}")
            elif property_name == "font-style" and value in ALLOWED_FONT_STYLE:
                declarations.append(f"font-style: {value}")
            elif property_name == "text-align" and value in ALLOWED_TEXT_ALIGN:
                declarations.append(f"text-align: {value}")
            elif property_name == "color" and (value in ALLOWED_COLOR_NAMES or COLOR_RE.match(value)):
                declarations.append(f"color: {value}")
        return "; ".join(declarations)


def sanitize_article_html(value):
    html = (value or "").strip()
    if not html:
        return ""

    if "<" not in html and ">" not in html:
        paragraphs = []
        for paragraph in html.split("\n\n"):
            lines = [escape(line.strip()) for line in paragraph.splitlines() if line.strip()]
            if lines:
                paragraphs.append(f"<p>{'<br>'.join(lines)}</p>")
        return "".join(paragraphs)

    parser = ArticleHtmlSanitizer()
    parser.feed(html)
    parser.close()
    sanitized = "".join(parser.parts).strip()
    if sanitized and not any(f"<{tag}" in sanitized.lower() for tag in BLOCK_TAGS):
        sanitized = f"<p>{sanitized}</p>"
    return sanitized


def render_safe_article_html(value):
    return mark_safe(sanitize_article_html(value))
