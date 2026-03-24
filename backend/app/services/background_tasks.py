import asyncio

from app.monitoring.metrics import Metrics
from app.repositories.redis_repository import RedisRepository
from app.services.event_dispatcher import ServerChannelSubscriber
from app.services.lobby_service import LobbyService


async def run_metrics_refresh_loop(
    repository: RedisRepository,
    lobby_service: LobbyService,
    metrics: Metrics,
    interval_seconds: int,
) -> None:
    while True:
        snapshot = await lobby_service.snapshot()
        waiting = int(snapshot.get("waiting_players", 0))
        active = await repository.active_matches_count()
        metrics.set_waiting_players(waiting)
        metrics.set_active_matches(active)
        await asyncio.sleep(interval_seconds)


async def run_pubsub_loop(subscriber: ServerChannelSubscriber) -> None:
    while True:
        await subscriber.pump_once()
        await asyncio.sleep(0.01)
