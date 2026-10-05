"""External MCP transport/ingress configuration extends provider/storage settings."""

import re
from typing import Literal

from flamoris_generation_controller.config import ModelKind as ModelKind
from flamoris_generation_controller.config import Settings as CoreSettings
from pydantic import Field, SecretStr, field_validator, model_validator


class Settings(CoreSettings):
    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    http_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    http_port: int = Field(default=8765, ge=1, le=65535)
    mcp_path: str = Field(default="/mcp", max_length=256)
    provenance_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)
    provenance_issuer: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    controller_token: SecretStr | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def validate_provenance_configuration(self):
        if (self.provenance_secret is None) != (self.provenance_issuer is None):
            raise ValueError("Provenance secret and expected issuer must be configured together")
        if self.provenance_secret is not None:
            token = self.provenance_secret.get_secret_value()
            if not 32 <= len(token) <= 512 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
                raise ValueError("Provenance secret must contain 32-512 credential characters")
        return self

    @field_validator("http_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("HTTP host must be a hostname or unbracketed IP address, not a URL")
        return value

    @field_validator("mcp_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value == "/api/v1/generation" or value.startswith("/api/v1/generation/"):
            raise ValueError("MCP path is reserved for the internal Controller API")
        if value == "/healthz":
            raise ValueError("MCP path /healthz is reserved for the HTTP liveness endpoint")
        if value != "/" and (
            not re.fullmatch(r"/[A-Za-z0-9_.~-]+(?:/[A-Za-z0-9_.~-]+)*", value)
            or any(segment in {".", ".."} for segment in value.split("/"))
        ):
            raise ValueError(
                "MCP path must be a literal absolute URL path without a trailing slash"
            )
        return value

    @field_validator("controller_token", mode="before")
    @classmethod
    def empty_controller_token(cls, value):
        # Compose's empty optional environment value disables only internal HTTP.
        return None if value == "" else value

    @field_validator("controller_token")
    @classmethod
    def validate_controller_token(cls, value):
        if value is not None:
            token = value.get_secret_value()
            if not 32 <= len(token) <= 512 or not all(33 <= ord(c) <= 126 for c in token):
                raise ValueError(
                    "Controller token must contain 32-512 printable credential characters"
                )
        return value

    env_fields = {
        **CoreSettings.env_fields,
        "MCP_TRANSPORT": "mcp_transport",
        "HTTP_HOST": "http_host",
        "HTTP_PORT": "http_port",
        "MCP_PATH": "mcp_path",
        "PROVENANCE_SECRET": "provenance_secret",
        "PROVENANCE_ISSUER": "provenance_issuer",
        "CONTROLLER_TOKEN": "controller_token",
    }
