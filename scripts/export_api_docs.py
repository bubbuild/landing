"""Export the server contract and Scalar reference for the documentation site."""

import json
from pathlib import Path

from scalar_fastapi import AgentScalarConfig, get_scalar_api_reference

from landing.server import create_app

output = Path("docs/assets/api")
output.mkdir(parents=True, exist_ok=True)
app = create_app(Path(":memory:"), token="documentation")  # noqa: S106 -- enables the documented bearer scheme; no server starts.
schema = app.openapi()
(output / "openapi.json").write_text(json.dumps(schema, indent=2) + "\n")
(output / "index.html").write_bytes(
    get_scalar_api_reference(
        content=schema,
        title="Landing API reference",
        hide_test_request_button=True,
        hide_client_button=True,
        telemetry=False,
        agent=AgentScalarConfig(disabled=True),
    ).body
)
