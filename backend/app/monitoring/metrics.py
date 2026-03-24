from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

WS_ACTIVE_CONNECTIONS = Gauge(
    "hangman_ws_active_connections",
    "Quantidade de conexoes websocket ativas",
    ["server"],
)
WAITING_PLAYERS = Gauge(
    "hangman_waiting_players",
    "Quantidade de jogadores aguardando na fila",
    ["server"],
)
ACTIVE_MATCHES = Gauge(
    "hangman_active_matches",
    "Quantidade de partidas ativas",
    ["server"],
)
MATCHES_FINISHED_TOTAL = Counter(
    "hangman_matches_finished_total",
    "Quantidade de partidas finalizadas",
    ["server", "reason"],
)
QUEUE_WAIT_SECONDS = Histogram(
    "hangman_queue_wait_seconds",
    "Tempo de espera na fila ate encontrar partida",
    ["server"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
RECONNECTIONS_TOTAL = Counter(
    "hangman_reconnections_total",
    "Quantidade de reconexoes de jogadores",
    ["server"],
)
DISCONNECTIONS_TOTAL = Counter(
    "hangman_disconnections_total",
    "Quantidade de desconexoes de jogadores",
    ["server"],
)
ERRORS_TOTAL = Counter(
    "hangman_errors_total",
    "Quantidade de erros emitidos para clientes",
    ["server", "type"],
)
BACKEND_UP = Gauge(
    "hangman_backend_up",
    "Sinalizador de saude do backend",
    ["server"],
)


class Metrics:
    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        BACKEND_UP.labels(server=server_id).set(1)

    def set_ws_connections(self, value: int) -> None:
        WS_ACTIVE_CONNECTIONS.labels(server=self.server_id).set(value)

    def set_waiting_players(self, value: int) -> None:
        WAITING_PLAYERS.labels(server=self.server_id).set(value)

    def set_active_matches(self, value: int) -> None:
        ACTIVE_MATCHES.labels(server=self.server_id).set(value)

    def inc_finished_matches(self, reason: str) -> None:
        MATCHES_FINISHED_TOTAL.labels(server=self.server_id, reason=reason).inc()

    def observe_queue_wait(self, seconds: float) -> None:
        QUEUE_WAIT_SECONDS.labels(server=self.server_id).observe(seconds)

    def inc_reconnections(self) -> None:
        RECONNECTIONS_TOTAL.labels(server=self.server_id).inc()

    def inc_disconnections(self) -> None:
        DISCONNECTIONS_TOTAL.labels(server=self.server_id).inc()

    def inc_errors(self, error_type: str = "generic") -> None:
        ERRORS_TOTAL.labels(server=self.server_id, type=error_type).inc()

    @staticmethod
    def render_latest() -> bytes:
        return generate_latest()

    @staticmethod
    def content_type() -> str:
        return CONTENT_TYPE_LATEST
