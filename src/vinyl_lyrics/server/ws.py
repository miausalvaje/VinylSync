"""WebSocket routes."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Push player state updates to a connected frontend."""
    await websocket.accept()
    runtime = websocket.app.state.runtime
    runtime.clients.add(websocket)
    try:
        await websocket.send_json(runtime.public_state())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.clients.discard(websocket)


@router.websocket("/ws/spectrum")
async def spectrum_endpoint(websocket: WebSocket) -> None:
    """Stream compact spectrum and level messages."""
    await websocket.accept()
    runtime = websocket.app.state.runtime
    runtime.spectrum_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.spectrum_clients.discard(websocket)
