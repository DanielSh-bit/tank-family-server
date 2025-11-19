import asyncio
import json
import math
import random
import time
import os
from aiohttp import web, WSMsgType

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
        self.sockets = {}  # Maps WebSocketResponse -> player_id
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
        # ב-aiohttp שליחת הודעה נעשית עם send_str
        # אנחנו מתעלמים משגיאות במקרה שמישהו התנתק באמצע
        to_remove = []
        for ws in self.sockets.keys():
            if ws.closed:
                to_remove.append(ws)
                continue
            try:
                await ws.send_str(data)
            except Exception:
                to_remove.append(ws)

        # ניקוי מהיר של מנותקים
        for ws in to_remove:
            self.remove_player(ws)

    def add_player(self, ws, name):
        if len(self.players) >= MAX_PLAYERS:
            return None, "FULL"

        if not self.colors_free:
            return None, "NO_COLORS"

        color = random.choice(list(self.colors_free))
        self.colors_free.remove(color)

        pid = f"p{len(self.players) + 1}-{random.randint(1000, 9999)}"
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


# --- הנדלרים של השרת ---

# 1. בדיקת בריאות (HTTP רגיל)
async def health_check(request):
    return web.Response(text="OK")


# 2. טיפול ב-Websocket
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    pid = None
    try:
        # המתנה להודעת הצטרפות ראשונה
        # ב-aiohttp קריאה להודעה היא דרך receive_str או בלולאה
        first_msg = await ws.receive()

        if first_msg.type == WSMsgType.TEXT:
            join_msg = json.loads(first_msg.data)

            if join_msg.get("type") != "join":
                await ws.close()
                return ws

            name = join_msg.get("name", "Player")
            player, err = game.add_player(ws, name)

            if err:
                await ws.send_str(json.dumps({"type": "error", "reason": err}))
                await ws.close()
                return ws

            pid = player.id
            # שליחת אישור הצטרפות
            await ws.send_str(json.dumps({
                "type": "joined",
                "id": pid,
                "color": player.color,
                "name": player.name
            }))
            await game.broadcast(game.get_state())

            # לולאת המשחק עבור הלקוח הזה
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "input":
                        dx = float(data.get("dir", {}).get("x", 0.0))
                        dy = float(data.get("dir", {}).get("y", 0.0))
                        fire = bool(data.get("fire", False))
                        game.update_inputs(pid, dx, dy, fire)
                elif msg.type == WSMsgType.ERROR:
                    print(f'ws connection closed with exception {ws.exception()}')

    finally:
        game.remove_player(ws)
        await game.broadcast(game.get_state())

    return ws


# --- לולאת המשחק ברקע ---
async def background_game_loop(app):
    try:
        while True:
            now = time.time()
            dt = now - game.last_tick
            game.last_tick = now
            game.tick(dt)
            await game.broadcast(game.get_state())
            await asyncio.sleep(1 / TICK_RATE)
    except asyncio.CancelledError:
        pass


# הפעלת המשימה ברקע כשהשרת עולה
async def on_startup(app):
    app['game_loop'] = asyncio.create_task(background_game_loop(app))


# ניקוי המשימה כשהשרת יורד
async def on_cleanup(app):
    app['game_loop'].cancel()
    await app['game_loop']


async def main():
    PORT = int(os.environ.get("PORT", 10000))

    app = web.Application()
    app.router.add_get('/', health_check)  # בשביל Render
    app.router.add_get('/ws', websocket_handler)  # אם הלקוח פונה ל-/ws

    # טיפ: אם הלקוח פונה לכתובת הראשית בלי נתיב, אפשר להוסיף גם:
    # app.router.add_get('/', websocket_handler)
    # אבל אז בדיקת הבריאות צריכה להיות חכמה יותר. עדיף להשאיר נתיבים נפרדים אם אפשר,
    # או שפשוט ננתב הכל ל-websocket handler והוא יחזיר שגיאה אם זה לא WS.

    # כדי לתמוך בקוד לקוח קיים שמתחבר ל-Root URL:
    # נגדיר שנתיב "/" יטפל גם בבריאות וגם ב-Websocket
    async def root_handler(request):
        # אם זו בקשת שדרוג ל-Websocket
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await websocket_handler(request)
        # אחרת, זו בקשת HTTP רגילה (כמו בדיקת בריאות)
        return await health_check(request)

    app = web.Application()
    # נתיב אחד ראשי שמטפל בהכל
    app.router.add_get('/', root_handler)

    # הגדרת משימות רקע
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    print(f"Server running on port {PORT}")
    await site.start()

    # מחזיק את השרת חי
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())