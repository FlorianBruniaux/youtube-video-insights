from __future__ import annotations

import hashlib
import importlib.util
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPOSITORY_ROOT / "src" / "yt_insights" / "web" / "static"
WEB_SOURCE_ROOT = REPOSITORY_ROOT / "web" / "src"
FORBIDDEN_BROWSER_TOKENS = (
    "innerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
)
PRIVATE_PATH_MARKERS = ("/Users/", "/home/", "C:\\Users\\")


class _PageContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.main_count = 0
        self.has_skip_link = False
        self.language: str | None = None
        self.has_title = False
        self.has_viewport = False
        self.scripts: list[dict[str, str | None]] = []
        self.inline_events: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "html":
            self.language = attributes.get("lang")
        elif tag == "main":
            self.main_count += 1
        elif tag == "a" and attributes.get("href") == "#main-content":
            self.has_skip_link = True
        elif tag == "title":
            self.has_title = True
        elif tag == "meta" and attributes.get("name") == "viewport":
            self.has_viewport = True
        elif tag == "script":
            self.scripts.append(attributes)
        self.inline_events.extend(name for name, _value in attrs if name.startswith("on"))


def _production_files() -> tuple[Path, ...]:
    return tuple(path for path in STATIC_ROOT.rglob("*") if path.is_file())


def test_generated_assets_are_local_path_free_and_exclude_maps() -> None:
    files = _production_files()

    assert files
    assert not tuple(STATIC_ROOT.rglob("*.map"))
    for path in files:
        assert path.is_relative_to(STATIC_ROOT)
        content = path.read_bytes()
        for marker in PRIVATE_PATH_MARKERS:
            assert marker.encode() not in content, path.relative_to(STATIC_ROOT)


@pytest.mark.parametrize("root", [WEB_SOURCE_ROOT, STATIC_ROOT])
def test_browser_code_avoids_unsafe_dom_apis_and_accidental_secrets(
    root: Path,
) -> None:
    secret_markers = (b"BEGIN PRIVATE KEY", b"sk-ant-", b"OPENAI_API_KEY=")
    for path in (item for item in root.rglob("*") if item.is_file()):
        content = path.read_bytes()
        for token in FORBIDDEN_BROWSER_TOKENS:
            assert token.encode() not in content, path.relative_to(root)
        for marker in secret_markers:
            assert marker not in content, path.relative_to(root)


def test_every_generated_page_has_one_accessible_local_only_shell() -> None:
    html_files = tuple(sorted(STATIC_ROOT.rglob("*.html")))

    assert html_files
    for path in html_files:
        parser = _PageContractParser()
        parser.feed(path.read_text(encoding="utf-8"))
        assert parser.main_count == 1, path.relative_to(STATIC_ROOT)
        assert parser.has_skip_link, path.relative_to(STATIC_ROOT)
        assert parser.language == "en", path.relative_to(STATIC_ROOT)
        assert parser.has_title, path.relative_to(STATIC_ROOT)
        assert parser.has_viewport, path.relative_to(STATIC_ROOT)
        assert not parser.inline_events, path.relative_to(STATIC_ROOT)
        assert parser.scripts, path.relative_to(STATIC_ROOT)
        for script in parser.scripts:
            source = script.get("src")
            assert source is not None and source.startswith("/_astro/")
            assert not source.startswith(("http://", "https://", "//"))


def test_web_build_verifier_uses_relative_hash_inventory() -> None:
    script = REPOSITORY_ROOT / "scripts" / "verify_web_build.py"
    specification = importlib.util.spec_from_file_location("verify_web_build", script)

    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    inventory = module.snapshot_tree(STATIC_ROOT, relative_to=REPOSITORY_ROOT)
    assert inventory
    assert all(not Path(name).is_absolute() for name in inventory)
    assert inventory == {
        path.relative_to(REPOSITORY_ROOT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in _production_files()
    }
