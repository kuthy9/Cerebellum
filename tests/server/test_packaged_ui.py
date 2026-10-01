from cerebellum.server.app import STATIC_DIR


def test_the_dashboard_ui_is_built_into_the_package():
    index = STATIC_DIR / "index.html"
    assert index.is_file(), "run `make ui` to build the dashboard"
    html = index.read_text(encoding="utf-8")
    assert "<title>Cerebellum</title>" in html and "/assets/" in html
    assert any(STATIC_DIR.joinpath("assets").glob("*.js"))
    assert any(STATIC_DIR.joinpath("assets").glob("*.css"))
