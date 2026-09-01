from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
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
REMOTE_CSS_ASSET = re.compile(
    rb"(?:url\(\s*|@import\s+)(?:[\"']\s*)?(?:https?:)?//",
    re.IGNORECASE,
)
PRIVATE_CHECKOUT_PATHS = (
    re.compile(rb"(?:file://)?/(?:Users|home|private|tmp|var/folders)/"),
    re.compile(rb"(?<![A-Za-z0-9])(?:file:///)?[a-z]:[\\/]", re.IGNORECASE),
)
REMOTE_PREFIXES = ("http://", "https://", "//")


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
        self.remote_assets: list[str] = []

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
        self._record_remote_assets(tag, attributes)
        self.inline_events.extend(name for name, _value in attrs if name.startswith("on"))

    def _record_remote_assets(
        self,
        tag: str,
        attributes: dict[str, str | None],
    ) -> None:
        candidates: list[str] = []
        if tag == "link":
            relations = set((attributes.get("rel") or "").lower().split())
            resource_type = (attributes.get("as") or "").lower()
            if "stylesheet" in relations or resource_type in {
                "audio",
                "font",
                "image",
                "video",
            }:
                candidates.append(attributes.get("href") or "")
        if tag in {"audio", "embed", "iframe", "img", "script", "source", "track", "video"}:
            candidates.append(attributes.get("src") or "")
        if tag == "video":
            candidates.append(attributes.get("poster") or "")
        if tag == "object":
            candidates.append(attributes.get("data") or "")
        if tag in {"img", "source"}:
            candidates.extend(
                item.strip().split(maxsplit=1)[0]
                for item in (attributes.get("srcset") or "").split(",")
                if item.strip()
            )
        self.remote_assets.extend(
            candidate
            for candidate in candidates
            if candidate.startswith(REMOTE_PREFIXES)
        )


def _production_files() -> tuple[Path, ...]:
    return tuple(path for path in STATIC_ROOT.rglob("*") if path.is_file())


def test_generated_assets_are_local_path_free_and_exclude_maps() -> None:
    files = _production_files()

    assert files
    assert not tuple(STATIC_ROOT.rglob("*.map"))
    for path in files:
        assert path.is_relative_to(STATIC_ROOT)
        content = path.read_bytes()
        assert not _private_checkout_paths(content), path.relative_to(STATIC_ROOT)
        if path.suffix == ".css":
            assert not _remote_css_assets(content), path.relative_to(STATIC_ROOT)


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
        assert not _private_checkout_paths(content), path.relative_to(root)
        if path.suffix == ".css":
            assert not _remote_css_assets(content), path.relative_to(root)


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
        assert not parser.remote_assets, path.relative_to(STATIC_ROOT)
        assert parser.scripts, path.relative_to(STATIC_ROOT)
        for script in parser.scripts:
            source = script.get("src")
            assert source is not None and source.startswith("/_astro/")
            assert not source.startswith(("http://", "https://", "//"))


@pytest.mark.parametrize(
    ("markup", "remote_url"),
    [
        (
            '<link rel="stylesheet" href="https://cdn.invalid/app.css">',
            "https://cdn.invalid/app.css",
        ),
        (
            '<link rel="preload" as="font" href="//cdn.invalid/app.woff2">',
            "//cdn.invalid/app.woff2",
        ),
        (
            '<video poster="https://cdn.invalid/poster.jpg">'
            '<source src="/local.mp4"></video>',
            "https://cdn.invalid/poster.jpg",
        ),
        (
            '<img src="/local.png" '
            'srcset="/local.png 1x, https://cdn.invalid/image.png 2x">',
            "https://cdn.invalid/image.png",
        ),
    ],
)
def test_html_scanner_flags_remote_stylesheet_font_and_media_urls(
    markup: str,
    remote_url: str,
) -> None:
    parser = _PageContractParser()

    parser.feed(markup)

    assert parser.remote_assets == [remote_url]


@pytest.mark.parametrize(
    "stylesheet",
    [
        b'@font-face { src: url("https://cdn.invalid/app.woff2"); }',
        b".hero { background-image: url(//cdn.invalid/poster.jpg); }",
        b'@import "https://cdn.invalid/theme.css";',
    ],
)
def test_css_scanner_flags_remote_runtime_assets(stylesheet: bytes) -> None:
    assert _remote_css_assets(stylesheet)


@pytest.mark.parametrize(
    "content",
    [
        b'"/private/var/folders/build/worktree/file.js"',
        b'"/tmp/yt-insights-checkout/file.js"',
        b'"/var/folders/cache/checkout/file.js"',
        b'"D:\\\\work\\\\yt-insights\\\\file.js"',
        b'"file:///Users/builder/checkout/file.js"',
    ],
)
def test_private_path_scanner_flags_unix_and_windows_checkouts(content: bytes) -> None:
    assert _private_checkout_paths(content)


def _remote_css_assets(content: bytes) -> tuple[bytes, ...]:
    return tuple(match.group(0) for match in REMOTE_CSS_ASSET.finditer(content))


def _private_checkout_paths(content: bytes) -> tuple[bytes, ...]:
    return tuple(
        match.group(0)
        for pattern in PRIVATE_CHECKOUT_PATHS
        for match in pattern.finditer(content)
    )


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


def test_web_build_verifier_builds_outside_checkout_and_leaves_success_clean(
    tmp_path: Path,
) -> None:
    module = _load_build_verifier()
    repository = _fixture_repository(tmp_path)
    before = module.snapshot_tree(repository, relative_to=repository)
    output_roots: list[Path] = []

    def build(
        staging_root: Path,
        output_root: Path,
    ) -> subprocess.CompletedProcess[list[str]]:
        assert staging_root != repository
        output_roots.append(output_root)
        assert not output_root.is_relative_to(repository)
        (output_root / "index.html").write_text("index-v1", encoding="utf-8")
        (output_root / "_astro").mkdir()
        (output_root / "_astro" / "app.12345678.js").write_text(
            "app-v1", encoding="utf-8"
        )
        return subprocess.CompletedProcess([], 0, "", "")

    assert module.verify_build(repository, run_build=build) == 0
    assert module.snapshot_tree(repository, relative_to=repository) == before
    assert len(output_roots) == 1
    assert not output_roots[0].exists()


def test_web_build_verifier_reports_complete_diff_without_mutating_checkout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_build_verifier()
    repository = _fixture_repository(tmp_path)
    before = module.snapshot_tree(repository, relative_to=repository)
    output_roots: list[Path] = []

    def build(
        staging_root: Path,
        output_root: Path,
    ) -> subprocess.CompletedProcess[list[str]]:
        assert staging_root != repository
        output_roots.append(output_root)
        (output_root / "index.html").write_text("index-v2", encoding="utf-8")
        (output_root / "extra.html").write_text("extra", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, "", "")

    assert module.verify_build(repository, run_build=build) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "committed web build is not reproducible:",
        "added: extra.html",
        "deleted: _astro/app.12345678.js",
        "modified: index.html",
    ]
    assert str(repository) not in captured.err
    assert module.snapshot_tree(repository, relative_to=repository) == before
    assert not output_roots[0].exists()


def test_web_build_verifier_cleans_temporary_output_after_build_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_build_verifier()
    repository = _fixture_repository(tmp_path)
    before = module.snapshot_tree(repository, relative_to=repository)
    output_roots: list[Path] = []

    def build(
        staging_root: Path,
        output_root: Path,
    ) -> subprocess.CompletedProcess[list[str]]:
        assert staging_root != repository
        output_roots.append(output_root)
        (output_root / "partial.html").write_text("partial", encoding="utf-8")
        return subprocess.CompletedProcess([], 7, "", "failure")

    assert module.verify_build(repository, run_build=build) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "web build failed; run `pnpm --dir web exec astro build` for diagnostics\n"
    )
    assert module.snapshot_tree(repository, relative_to=repository) == before
    assert not output_roots[0].exists()


def test_web_build_verifier_isolates_runner_source_mutations(
    tmp_path: Path,
) -> None:
    module = _load_build_verifier()
    repository = _fixture_repository(tmp_path)
    source = repository / "web" / "src" / "index.ts"
    source.parent.mkdir()
    source.write_text("export const original = true;", encoding="utf-8")
    before = module.snapshot_tree(repository, relative_to=repository)
    staging_roots: list[Path] = []

    def build(
        staging_root: Path,
        output_root: Path,
    ) -> subprocess.CompletedProcess[list[str]]:
        staging_roots.append(staging_root)
        injected = staging_root / "web" / "src" / "injected.ts"
        injected.write_text("export const injected = true;", encoding="utf-8")
        (output_root / "index.html").write_text("index-v1", encoding="utf-8")
        (output_root / "_astro").mkdir()
        (output_root / "_astro" / "app.12345678.js").write_text(
            "app-v1", encoding="utf-8"
        )
        return subprocess.CompletedProcess([], 0, "", "")

    assert module.verify_build(repository, run_build=build) == 0
    assert not (repository / "web" / "src" / "injected.ts").exists()
    assert module.snapshot_tree(repository, relative_to=repository) == before
    assert len(staging_roots) == 1
    assert not staging_roots[0].exists()


def _load_build_verifier():
    script = REPOSITORY_ROOT / "scripts" / "verify_web_build.py"
    specification = importlib.util.spec_from_file_location("verify_web_build", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _fixture_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "checkout"
    static = repository / "src" / "yt_insights" / "web" / "static"
    (static / "_astro").mkdir(parents=True)
    (static / "index.html").write_text("index-v1", encoding="utf-8")
    (static / "_astro" / "app.12345678.js").write_text(
        "app-v1", encoding="utf-8"
    )
    (repository / "web").mkdir()
    (repository / "web" / "package.json").write_text("{}", encoding="utf-8")
    return repository
