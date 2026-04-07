import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    app_name: str
    server_id: str
    redis_url: str
    reconnect_timeout_seconds: int
    server_heartbeat_ttl_seconds: int
    server_heartbeat_interval_seconds: float
    max_errors: int
    queue_key: str
    lobby_rooms_set_key: str
    default_room_count: int
    active_matches_key: str
    reconnect_zset_key: str
    heartbeat_ttl_seconds: int
    metrics_refresh_seconds: int
    deadline_check_seconds: int
    websocket_path: str
    words_file: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Distributed Hangman"),
        server_id=os.getenv("SERVER_ID", "game-server"),
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        reconnect_timeout_seconds=int(os.getenv("RECONNECT_TIMEOUT_SECONDS", "30")),
        server_heartbeat_ttl_seconds=int(os.getenv("SERVER_HEARTBEAT_TTL_SECONDS", "3")),
        server_heartbeat_interval_seconds=float(os.getenv("SERVER_HEARTBEAT_INTERVAL_SECONDS", "1")),
        max_errors=int(os.getenv("MAX_ERRORS", "6")),
        queue_key=os.getenv("QUEUE_KEY", "queue:waiting_players"),
        lobby_rooms_set_key=os.getenv("LOBBY_ROOMS_SET_KEY", "lobby:rooms"),
        default_room_count=int(os.getenv("DEFAULT_ROOM_COUNT", "9")),
        active_matches_key=os.getenv("ACTIVE_MATCHES_KEY", "matches:active"),
        reconnect_zset_key=os.getenv("RECONNECT_ZSET_KEY", "reconnect:deadlines"),
        heartbeat_ttl_seconds=int(os.getenv("HEARTBEAT_TTL_SECONDS", "60")),
        metrics_refresh_seconds=int(os.getenv("METRICS_REFRESH_SECONDS", "5")),
        deadline_check_seconds=int(os.getenv("DEADLINE_CHECK_SECONDS", "1")),
        websocket_path=os.getenv("WEBSOCKET_PATH", "/ws"),
        words_file=os.getenv("WORDS_FILE", "app/data/words.txt"),
    )
