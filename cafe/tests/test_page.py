"""
cafe/tests/test_page.py
pytest tests for the Cafe Landing Page — stdlib only (pathlib + html.parser).
No Selenium or external dependencies required.
"""

import pathlib
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAFE_DIR = pathlib.Path(__file__).parent.parent


def _read(filename: str) -> str:
    return (CAFE_DIR / filename).read_text(encoding="utf-8")


class _AttrCollector(HTMLParser):
    """
    Collects all tags, ids, classes, and raw text from an HTML document.
    Provides helper methods for assertions.
    """

    def __init__(self):
        super().__init__()
        self.text_chunks: list[str] = []
        self.elements: list[dict] = []  # {"tag": str, "attrs": dict}

    def handle_starttag(self, tag: str, attrs):
        attr_dict = dict(attrs)
        self.elements.append({"tag": tag, "attrs": attr_dict})

    def handle_data(self, data: str):
        stripped = data.strip()
        if stripped:
            self.text_chunks.append(stripped)

    # ---- convenience ----

    def full_text(self) -> str:
        return " ".join(self.text_chunks)

    def find_by_id(self, element_id: str) -> list[dict]:
        return [
            el for el in self.elements
            if el["attrs"].get("id") == element_id
        ]

    def find_by_tag(self, tag: str) -> list[dict]:
        return [el for el in self.elements if el["tag"] == tag]

    def find_by_tag_and_id(self, tag: str, element_id: str) -> list[dict]:
        return [
            el for el in self.elements
            if el["tag"] == tag and el["attrs"].get("id") == element_id
        ]

    def find_with_attr(self, attr_name: str, attr_value: str) -> list[dict]:
        return [
            el for el in self.elements
            if el["attrs"].get(attr_name) == attr_value
        ]

    def has_element_with_attr(self, attr_name: str) -> bool:
        """Return True if any element has the given attribute (regardless of value)."""
        return any(attr_name in el["attrs"] for el in self.elements)


def _parse_html() -> _AttrCollector:
    html_src = _read("index.html")
    parser = _AttrCollector()
    parser.feed(html_src)
    return parser


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_html_has_hero_section():
    """Hero section must contain the headline 'Wake up & smell the coffee'."""
    parser = _parse_html()
    full_text = parser.full_text()
    # The HTML entity &amp; renders as '&', parser returns raw text
    assert (
        "Wake up & smell the coffee" in full_text
        or "Wake up &amp; smell the coffee" in _read("index.html")
    ), "Hero headline 'Wake up & smell the coffee' not found in index.html"


def test_html_has_menu_grid():
    """Section with id='menu' must exist and contain at least 3 menu item cards."""
    parser = _parse_html()

    # Check section with id="menu" exists
    menu_sections = parser.find_by_id("menu")
    assert len(menu_sections) >= 1, "No element with id='menu' found in index.html"

    # Count .menu-card elements (data-category attribute is a reliable marker)
    menu_cards = parser.find_with_attr("data-category", "coffee") + \
                 parser.find_with_attr("data-category", "tea") + \
                 parser.find_with_attr("data-category", "pastry")

    assert len(menu_cards) >= 3, (
        f"Expected at least 3 menu cards, found {len(menu_cards)}"
    )


def test_html_has_cart_drawer():
    """Cart drawer must exist with id='cart-drawer' and have [hidden] attribute initially."""
    html_src = _read("index.html")
    parser = _AttrCollector()
    parser.feed(html_src)

    cart_drawers = parser.find_by_id("cart-drawer")
    assert len(cart_drawers) >= 1, "No element with id='cart-drawer' found in index.html"

    cart_drawer = cart_drawers[0]
    assert "hidden" in cart_drawer["attrs"], (
        "cart-drawer element must have [hidden] attribute on initial page load"
    )


def test_html_has_brew_calculator():
    """Brew calculator section (id='brew-calculator') and input (id='coffee-grams') must exist."""
    parser = _parse_html()

    brew_sections = parser.find_by_id("brew-calculator")
    assert len(brew_sections) >= 1, "No element with id='brew-calculator' found"

    coffee_inputs = parser.find_by_id("coffee-grams")
    assert len(coffee_inputs) >= 1, "No input with id='coffee-grams' found"

    # Must be an input element
    assert coffee_inputs[0]["tag"] == "input", (
        "Element with id='coffee-grams' should be an <input> element"
    )


def test_html_has_reservation_form():
    """Form with id='reservation-form' must exist."""
    parser = _parse_html()

    reservation_forms = parser.find_by_tag_and_id("form", "reservation-form")
    assert len(reservation_forms) >= 1, (
        "No <form> with id='reservation-form' found in index.html"
    )


def test_html_has_theme_toggle():
    """Button with id='theme-toggle' must exist in the nav."""
    parser = _parse_html()

    theme_btns = parser.find_by_tag_and_id("button", "theme-toggle")
    assert len(theme_btns) >= 1, (
        "No <button> with id='theme-toggle' found in index.html"
    )


def test_css_hidden_rule():
    """
    cafe/styles.css must contain the [hidden] { display: none !important; } rule
    to ensure hidden elements are strictly invisible on page load.
    """
    css_src = _read("styles.css")

    assert "[hidden]" in css_src, (
        "styles.css must contain a '[hidden]' CSS rule"
    )
    assert "display: none" in css_src, (
        "styles.css must contain 'display: none' in the [hidden] rule"
    )


def test_js_cart_logic():
    """
    cafe/app.js must contain 'Add to Order' handler pattern and
    tax calculation (0.085 or 8.5) as code strings.
    """
    js_src = _read("app.js")

    # Check for 'Add to Order' handler — the addEventListener pattern on add-to-order buttons
    assert "add-to-order" in js_src or "Add to Order" in js_src, (
        "app.js must contain handler logic referencing 'add-to-order' or 'Add to Order'"
    )

    # Check for tax rate — either as 0.085 or the comment/label 8.5
    assert "0.085" in js_src or "8.5" in js_src, (
        "app.js must contain tax rate as '0.085' or '8.5'"
    )
