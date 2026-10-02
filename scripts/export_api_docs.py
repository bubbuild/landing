"""Export the server's OpenAPI contract for the documentation site."""

import json
from pathlib import Path

from landing.server import create_app

output = Path("docs/assets/api")
output.mkdir(parents=True, exist_ok=True)
app = create_app(Path(":memory:"), token="documentation")  # noqa: S106 -- enables the documented bearer scheme; no server starts.
schema = app.openapi()
(output / "openapi.json").write_text(json.dumps(schema, indent=2) + "\n")
