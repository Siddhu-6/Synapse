from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNAPSE_", env_file=".env", extra="ignore")

    llm_provider: Literal["ollama", "openai"] = "ollama"
    model: str = "qwen2.5:7b-instruct"
    embed_model: str = "nomic-embed-text"
    ollama_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.groq.com/openai/v1"
    openai_api_key: SecretStr | None = None
    llm_timeout: float = 180.0
    # Optional fast provider (e.g. Groq free tier). When set, planning/writing/answering use it and
    # local Ollama stays available as the fallback — usually 10-30x faster than a local 7B.
    fast_base_url: str | None = None
    fast_model: str | None = None
    fast_api_key: SecretStr | None = None
    num_ctx: int = 8192              # Ollama context window; larger = slower prompt processing
    keep_alive: str = "30m"          # keep the model resident between calls (avoids reload stalls)
    reasoning_effort: Literal["low", "medium", "high"] | None = "low"   # gpt-oss thinking budget (hosted)
    plan_max_tokens: int = 700
    gen_max_tokens: int = 1200

    vault_path: Path = Path("./SynapseVault")
    data_dir: Path = Path("./data")

    tool_timeout: float = 30.0
    max_steps: int = 6
    parallel_steps: int = 2          # independent, auto-approved steps run concurrently
    max_replans: int = 1
    max_llm_calls: int = 20          # per-run budget, guards against runaway loops
    crew_timeout: float = 420.0
    enable_crew: bool = True
    enable_scheduler: bool = True
    scheduler_interval: float = 30.0
    notion_token: SecretStr | None = None     # https://www.notion.so/my-integrations
    n8n_webhooks: str = ""                    # name=https://...,other=https://... (allowlist)
    n8n_secret: SecretStr | None = None       # sent as X-Synapse-Secret; check it in n8n (Header Auth)
    crew_critic: bool = False        # 3rd agent doubles crew latency; opt in for important reports
    enable_web: bool = True

    # Who drafts are written for. Optional: without these, Synapse uses what you told it ("my name is ...").
    user_name: str | None = None
    user_email: str | None = None

    api_token: SecretStr | None = None  # if set, the API requires `Authorization: Bearer <token>`
    host: str = "127.0.0.1"
    port: int = 8000

    @field_validator("openai_api_key", "fast_api_key", "fast_base_url", "fast_model", "notion_token",
                     "n8n_secret", "api_token", "user_name", "user_email", mode="before")
    @classmethod
    def _blank_is_unset(cls, v):
        """`KEY=   # comment` in .env arrives as the comment text itself. An empty or comment-only
        value means "not set" — otherwise the comment gets sent to Groq as an API key."""
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if isinstance(v, str):
            v = v.strip()
            if not v or v.startswith("#"):
                return None
        return v

    @property
    def fast_enabled(self) -> bool:
        return bool(self.fast_api_key and self.fast_base_url and self.fast_model)

    @property
    def google_token_path(self) -> Path:
        return self.data_dir / "google_token.json"

    def ensure_dirs(self) -> None:
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
