import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging

from fastapi import FastAPI, Response, WebSocket
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.redis import close_redis, get_redis, init_redis
from app.monitoring.metrics import Metrics
from app.repositories.redis_repository import RedisRepository
from app.services.background_tasks import run_metrics_refresh_loop, run_pubsub_loop, run_server_heartbeat_loop
from app.services.connection_manager import ConnectionManager
from app.services.event_dispatcher import EventDispatcher, ServerChannelSubscriber
from app.services.game_service import GameService
from app.services.lobby_service import LobbyService
from app.services.matchmaking_service import MatchmakingService
from app.services.timeout_service import TimeoutService
from app.services.word_bank import WordBank
from app.websocket.handlers import websocket_handler

logger = logging.getLogger("hangman.app")


@dataclass
class AppContainer:
    settings: Settings
    metrics: Metrics
    repository: RedisRepository
    connection_manager: ConnectionManager
    dispatcher: EventDispatcher
    matchmaking: MatchmakingService
    game_service: GameService
    lobby_service: LobbyService
    timeout_service: TimeoutService
    subscriber: ServerChannelSubscriber
    background_tasks: list[asyncio.Task]


container: AppContainer | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global container
    settings = get_settings()
    setup_logging(settings.server_id)
    logger.info(
        "backend_starting",
        extra={"event": "backend_starting", "server_id": settings.server_id},
    )
    await init_redis()
    redis = get_redis()

    metrics = Metrics(settings.server_id)
    repository = RedisRepository(redis, settings)
    connection_manager = ConnectionManager(metrics)
    dispatcher = EventDispatcher(settings, repository, connection_manager)
    word_bank = WordBank(settings.words_file)
    matchmaking = MatchmakingService(settings, repository, dispatcher, metrics, word_bank)
    lobby_service = LobbyService(settings, repository)
    game_service = GameService(
        settings,
        repository,
        dispatcher,
        connection_manager,
        matchmaking,
        lobby_service,
        word_bank,
        metrics,
    )
    timeout_service = TimeoutService(
        repository,
        game_service,
        check_interval_seconds=settings.deadline_check_seconds,
    )
    subscriber = ServerChannelSubscriber(settings, repository, connection_manager)
    await subscriber.start()
    await lobby_service.ensure_default_rooms()

    background_tasks = [
        asyncio.create_task(run_pubsub_loop(subscriber)),
        asyncio.create_task(
            run_server_heartbeat_loop(
                repository,
                settings.server_id,
                interval_seconds=settings.server_heartbeat_interval_seconds,
            )
        ),
        asyncio.create_task(
            run_metrics_refresh_loop(
                repository,
                lobby_service,
                metrics,
                interval_seconds=settings.metrics_refresh_seconds,
            )
        ),
        asyncio.create_task(timeout_service.run()),
    ]
    container = AppContainer(
        settings=settings,
        metrics=metrics,
        repository=repository,
        connection_manager=connection_manager,
        dispatcher=dispatcher,
        matchmaking=matchmaking,
        game_service=game_service,
        lobby_service=lobby_service,
        timeout_service=timeout_service,
        subscriber=subscriber,
        background_tasks=background_tasks,
    )

    try:
        yield
    finally:
        timeout_service.stop()
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await subscriber.stop()
        await close_redis()
        logger.info(
            "backend_stopped",
            extra={"event": "backend_stopped", "server_id": settings.server_id},
        )
        container = None


app = FastAPI(title="Distributed Hangman", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    if container is None:
        return {"status": "starting"}
    return {"status": "ok", "server_id": container.settings.server_id}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=Metrics.render_latest(), media_type=Metrics.content_type())


@app.get("/lobby")
async def lobby() -> dict:
    if container is None:
        return {
            "timestamp": 0,
            "total_rooms": 0,
            "active_matches": 0,
            "waiting_players": 0,
            "waiting_rooms": 0,
            "rooms": [],
        }
    data = await container.lobby_service.snapshot()
    logger.debug(
        "lobby_snapshot_requested",
        extra={
            "event": "lobby_snapshot_requested",
            "active_matches": data["active_matches"],
            "waiting_players": data["waiting_players"],
        },
    )
    return data


class CreateRoomPayload(BaseModel):
    name: str


@app.post("/lobby/rooms")
async def create_room(payload: CreateRoomPayload) -> dict:
    if container is None:
        return {"error": "Servidor inicializando"}
    room = await container.lobby_service.create_room(payload.name)
    return {"room": room}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    if container is None:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "Servidor inicializando"})
        await websocket.close(code=1013)
        return
    await websocket_handler(websocket, container.game_service)
