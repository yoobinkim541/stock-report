from pathlib import Path
import tomllib


def test_vercel_flask_entrypoint_is_declared():
    path = Path("pyproject.toml")

    assert path.exists()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["tool"]["vercel"]["entrypoint"] == "portfolio_sync_server:app"
