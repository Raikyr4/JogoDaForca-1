import asyncio
import time

from app.repositories.redis_repository import RedisRepository
from app.services.game_service import GameService


class TimeoutService:
    def __init__(
        self,
        repository: RedisRepository,
        game_service: GameService,
        check_interval_seconds: int = 1,
    ) -> None:
        self.repository = repository
        self.game_service = game_service
        self.check_interval_seconds = check_interval_seconds
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            now = int(time.time())
            tokens = await self.repository.read_expired_deadlines(now)
            for token in tokens:
                parts = token.split(":")
                if len(parts) != 2:
                    continue
                match_id, player_id = parts
                await self.game_service.resolve_expired_deadline(match_id, player_id)
            await self.game_service.recover_players_from_dead_servers()
            await asyncio.sleep(self.check_interval_seconds)

    def stop(self) -> None:
        self._running = False
