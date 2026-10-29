"""应用配置，从环境变量加载。"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None if os.environ.get('JELLYFISH_SKIP_ENV_FILE') == '1' else str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Fawnimo API"
    debug: bool = False

    # API
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./jellyfish.db"
    # A tenant process is permanently bound to one server-owned database and root.
    workspace_id: str | None = Field(None, pattern=r'^ws_[a-f0-9]{32}$')
    workspace_internal_token: str | None = None
    workspace_runtime_locked: bool = False

    # Redis / Celery Broker
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    celery_broker_url: str | None = None

    # CORS：环境变量中建议使用逗号分隔（更贴近 docker-compose 用法）
    # 也兼容 JSON 数组：'["http://a","http://b"]'
    cors_origins: str = "http://localhost:7788,http://127.0.0.1:7788"

    @property
    def cors_origins_list(self) -> list[str]:
        s = (self.cors_origins or "").strip()
        if not s:
            return []
        if s.startswith("["):
            loaded = json.loads(s)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
            return []
        return [x.strip() for x in s.split(",") if x.strip()]

    # Private local file storage is usable without a cloud account. Auto retains configured S3.
    storage_backend: Literal["auto", "local", "s3"] = "auto"
    local_storage_root: str = str(BACKEND_ROOT / "local-files")
    local_storage_max_bytes: int = Field(64 * 1024 * 1024, ge=1)

    # S3 / 对象存储（用于素材文件）
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str | None = None
    # 可选：统一前缀，方便按环境/项目隔离，如 "jellyfish/dev"
    s3_base_path: str = ""
    # 可选：对外访问基址（CDN 或自定义域名），为空则使用 S3 自带 URL 或预签名 URL
    s3_public_base_url: str | None = None
    # Dedicated project media root. Client filenames are never used as disk paths.
    production_media_root: str = str(BACKEND_ROOT / "local-media")
    production_upload_limit: int = 64 * 1024 * 1024
    production_probe_executable: str = ""
    reverse_analysis_enabled: bool = True
    editing_export_enabled: bool = True
    reverse_media_allowed_hosts: str = ""
    # Explicit server-owned video execution configuration; omitted means preparation only.
    video_runtime_config: str | None = None
    # Understanding jobs are explicit and local by default; remote calls need server opt-in.
    video_understanding_enabled: bool = True
    video_understanding_allow_remote: bool = False

    def model_post_init(self, __context: object) -> None:
        if not self.celery_broker_url or not str(self.celery_broker_url).strip():
            password_part = f":{self.redis_password}@" if self.redis_password else ""
            self.celery_broker_url = f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
