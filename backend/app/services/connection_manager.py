import asyncio
from typing import Optional

from fastapi import WebSocket

from app.monitoring.metrics import Metrics


class ConnectionManager:
    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics
        self._player_to_ws: dict[str, WebSocket] = {}
        self._ws_to_player: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def bind_player(self, player_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            previous_ws = self._player_to_ws.get(player_id)
            if previous_ws is not None and previous_ws is not websocket:
                try:
                    await previous_ws.close(code=4001)
                except Exception:
                    pass
                self._ws_to_player.pop(previous_ws, None)

            previous_player = self._ws_to_player.get(websocket)
            if previous_player is not None and previous_player != player_id:
                self._player_to_ws.pop(previous_player, None)

            self._player_to_ws[player_id] = websocket
            self._ws_to_player[websocket] = player_id
            self.metrics.set_ws_connections(len(self._player_to_ws))

    async def unbind_websocket(self, websocket: WebSocket) -> Optional[str]:
        async with self._lock:
            player_id = self._ws_to_player.pop(websocket, None)
            if player_id is not None:
                current_ws = self._player_to_ws.get(player_id)
                if current_ws is websocket:
                    self._player_to_ws.pop(player_id, None)
            self.metrics.set_ws_connections(len(self._player_to_ws))
            return player_id

    async def send_local(self, player_id: str, payload: dict) -> bool:
        websocket = self._player_to_ws.get(player_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            await self.unbind_websocket(websocket)
            return False
