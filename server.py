import asyncio
import json
import math
import random
import time
import websockets
import os   # חשוב!

MAX_PLAYERS = 6
TICK_RATE = 30
PLAYER_SPEED = 150.0
ROT_SPEED = 3.0

COLORS = ["red", "green", "blue", "pink", "black", "orange"]

class Player:
    def __init__(self, pid, name, color):
        self.id = pid
        self.name = name
        self.color = color
        self.x = random.randint(100, 500)
        self.y = random.randint(100, 500)
        self.angle = 0.0
        self.alive = True
        self.dir_x = 0.0
        self.dir_y = 0.0
        self.want_fire = False
        self.score = 0
        self.deaths = 0
        self.games = 0

class Game:
    def __init__(self):
        self.players = {}
        self.sockets = {}
        self.colors_free = set(COLORS)
        self.round_running = False
        self.last_tick = time.time()

    def get_state(self):
        return {
            "type": "state",
            "round_running": self.round_running,
            "players": {
                pid: {
                    "name": p.name,
                    "x": p.x,
                    "y": p.y,
                    "angle": p.angle,
                    "color": p.color,
                    "alive": p.alive,
                    "score": p.score,
                    "deaths": p.deaths,
                    "games": p.games,
                }
                for pid, p in self.players.items()
            },
        }

    async def broadcast(self, msg):
        if not self.sockets:
            return
        data = json.dumps(msg)
        await asyncio.gather(
            *[ws.send(data) for ws in self.sockets.keys()],
            return_exceptions=True
        )

    def add_player(self, ws, name):
        if len(self.players) >= MAX_PLAYERS:
            return None, "FULL"

        if not self.colors_free:
            return None, "NO_COLORS"

        color = random.choice(list(self.colors_free))
        self.colors_free.remove(color)

        pid = f"p{len(self.players)+1}-{random.randint(1000,9999)}"
        p = Player(pid, name, color)
        self.players[pid] = p
        self.sockets[ws] = pid
        return p, None

    def remove_player(self, ws):
        pid = self.sockets.get(ws)
        if not pid:
            return
        p = self.players.pop(pid, None)
        if p:
            self.colors_free.add(p.color)
        self.sockets.pop(ws, None)

    def update_inputs(self, pid, dir_x, dir_y, fire):
        p = self.players.get(pid)
        if not p:
            return
        p.dir_x = dir_x
        p.dir_y = dir_y
        p.want_fire = fire

    def tick(self, dt):
        if len(self.players) < 2:
            self.round_running = False
        else:
            self.round_running = True

        for p in self.players.values():
            if not p.alive:
                continue
            p.angle += ROT_SPEED * p.dir_x * dt
            forward = -p.dir_y
            p.x += math.cos(p.angle) * PLAYER_SPEED * forward * dt
            p.y += math.sin(p.angle) * PLAYER_SPEED * forward * dt

game = Game()

async def handle_client(ws):
    pid = None
    try:
        join_raw = await ws.recv()
        join_msg = json.loads(join_raw)
        if join_msg.get("type") != "join":
            await ws.close()
            return

        name = join_msg.get("name", "Player")
        player, err = game.add_player(ws, name)
        if err:
            await ws.send(json.dumps({"type": "error", "reason": err}))
            await ws.close()
            return

        pid = player.id
        await ws.send(json.dumps({
            "type": "joined",
            "id": pid,
            "color": player.color,
            "name": player.name
        }))
        await game.broadcast(game.get_state())

        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "input":
                dx = float(msg.get("dir", {}).get("x", 0.0))
                dy = float(msg.get("dir", {}).get("y", 0.0))
                fire = bool(msg.get("fire", False))
                game.update_inputs(pid, dx, dy, fire)

    except websockets.ConnectionClosed:
        pass
    finally:
        game.remove_player(ws)
        await game.broadcast(game.get_state())

async def game_loop():
    while True:
        now = time.time()
        dt = now - game.last_tick
        game.last_tick = now
        game.tick(dt)
        await game.broadcast(game.get_state())
        await asyncio.sleep(1 / TICK_RATE)

async def main():
    PORT = int(os.environ.get("PORT", 10000))   # ⭐ Render chooses the port!
    async with websockets.serve(handle_client, "0.0.0.0", PORT):
        print(f"Server running on port {PORT}")
        await game_loop()

if __name__ == "__main__":
    asyncio.run(main())
