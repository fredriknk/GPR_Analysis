"""Offline controller packaging; scientific arrays are computed in Python."""

from importlib.metadata import distribution
from pathlib import Path

from plotly.offline import get_plotlyjs


def write_viewer(output: Path, payload_json: str):
    """No fetch/CDN or server is required, including when opened using file://."""
    templates = Path(__file__).parent / "assets"
    assets = output / "assets"
    assets.mkdir()
    (assets / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    package = distribution("plotly")
    license_file = next(p for p in package.files if str(p).endswith("licenses/LICENSE.txt"))
    (assets / "plotly.LICENSE.txt").write_bytes(package.locate_file(license_file).read_bytes())
    (assets / "viewer.js").write_bytes((templates / "viewer.js").read_bytes())
    safe = payload_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = (templates / "viewer.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(template.replace("__SCENES__", safe), encoding="utf-8")
