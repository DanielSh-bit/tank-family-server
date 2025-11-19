import asyncio
import json
import math
import random
import time
from aiohttp import web, WSMsgType

# --- הגדרות המשחק ---
TANK_SPEED = 2.0
BULLET_SPEED = 5.0
BULLET_RADIUS = 3
TANK_RADIUS = 15
GAME_WIDTH = 600
GAME_HEIGHT = 600
MIN_PLAYERS_TO_START = 2

# רשימת הצבעים הפנויים
AVAILABLE_COLORS = [
    "red", "blue", "green", "pink", "orange", "yellow", "cyan", "magenta"
]

# --- מבני נתונים ---
players = {}
lobby_connections = {}  # חיבורי WS של שחקנים בלובי (לעדכון סטטוס לובי)
game_state = "waiting"  # 'waiting', 'playing', 'session_end'
bullets = []
last_bullet_id = 0
last_game_update = time.time()
game_start_time = 0


class Player:
    def __init__(self, player_id, name, color, initial_stats=None):
        self.id = player_id
        self.name = name
        self.color = color
        self.x = random.randint(TANK_RADIUS, GAME_WIDTH - TANK_RADIUS)
        self.y = random.randint(TANK_RADIUS, GAME_HEIGHT - TANK_RADIUS)
        self.angle = 0.0
        self.alive = True
        self.stats = initial_stats or {"kills": 0, "wins": 0, "play_time": 0}
        self.last_fire_time = 0
        self.move_x = 0.0
        self.move_y = 0.0

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "x": self.x,
            "y": self.y,
            "angle": self.angle,
            "alive": self.alive,
            "stats": self.stats
        }


class Bullet:
    def __init__(self, bullet_id, owner_id, x, y, angle):
        self.id = bullet_id
        self.owner_id = owner_id
        self.x = x
        self.y = y
        self.angle = angle
        self.vx = math.cos(angle) * BULLET_SPEED
        self.vy = math.sin(angle) * BULLET_SPEED
        self.bounces = 0
        self.max_bounces = 1  # הוספת הגבלת ריבאונד

    def to_dict(self):
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "x": self.x,
            "y": self.y,
        }


# --- פונקציות עזר ---

def get_available_color():
    """מוצא את הצבע הפנוי הראשון ברשימה"""
    used_colors = {p.color for p in players.values()}
    for color in AVAILABLE_COLORS:
        if color not in used_colors:
            return color
    return AVAILABLE_COLORS[0]  # אם אין מקום, מחזיר את הראשון (לא אידיאלי)


def get_lobby_state():
    """מחזיר את הסטטוס הנוכחי של הלובי"""
    num_players = len(players)
    return {
        "type": "lobby_state",
        "game_state": game_state,
        "num_players": num_players,
        "players": {p_id: p.to_dict() for p_id, p in players.items()}
    }


def start_new_game():
    """מאפס את המצב ומתחיל משחק חדש"""
    global game_state, bullets, game_start_time

    if len(players) < MIN_PLAYERS_TO_START:
        print("Cannot start game: too few players.")
        return

    game_state = "playing"
    bullets = []
    game_start_time = time.time()

    # מיקום טנקים מחדש (כאן תוצב בהמשך לוגיקת מיקום מפות)
    for p in players.values():
        p.alive = True
        p.x = random.randint(TANK_RADIUS, GAME_WIDTH - TANK_RADIUS)
        p.y = random.randint(TANK_RADIUS, GAME_HEIGHT - TANK_RADIUS)

    print("Game started!")


async def broadcast_lobby_state():
    """שולח את מצב הלובי לכל החיבורים הפעילים בלובי"""
    state = json.dumps(get_lobby_state())
    disconnected_websockets = []
    for ws in lobby_connections.keys():
        if not ws.closed:
            await ws.send_str(state)
        else:
            disconnected_websockets.append(ws)

    # ניקוי חיבורים שנותקו
    for ws in disconnected_websockets:
        lobby_connections.pop(ws, None)


# --- לוגיקת משחק (פשוטה לשלב זה) ---

def update_game_physics(dt):
    """עדכון מיקומי שחקנים וקליעים"""

    # 1. עדכון שחקנים (בדיקת גבולות פשוטה)
    for p in players.values():
        if p.alive:
            new_x = p.x + p.move_x * TANK_SPEED * dt
            new_y = p.y + p.move_y * TANK_SPEED * dt

            # בדיקת גבולות (מעבר פשוט)
            if TANK_RADIUS <= new_x <= GAME_WIDTH - TANK_RADIUS:
                p.x = new_x
            if TANK_RADIUS <= new_y <= GAME_HEIGHT - TANK_RADIUS:
                p.y = new_y

    # 2. עדכון קליעים (ובדיקת גבולות/פגיעה)
    global bullets
    new_bullets = []
    for b in bullets:
        # תנועה
        b.x += b.vx * dt
        b.y += b.vy * dt

        # בדיקת גבולות העולם (ריבאונד פשוט)
        hit_wall = False
        if b.x - BULLET_RADIUS < 0 or b.x + BULLET_RADIUS > GAME_WIDTH:
            b.vx *= -1
            hit_wall = True
        if b.y - BULLET_RADIUS < 0 or b.y + BULLET_RADIUS > GAME_HEIGHT:
            b.vy *= -1
            hit_wall = True

        if hit_wall:
            b.bounces += 1

        # בדיקת פגיעה בשחקנים
        hit_player = False
        for p in players.values():
            if p.alive and p.id != b.owner_id:
                distance = math.sqrt((p.x - b.x) ** 2 + (p.y - b.y) ** 2)
                if distance < TANK_RADIUS + BULLET_RADIUS:
                    # פגיעה!
                    p.alive = False
                    if b.owner_id in players:
                        players[b.owner_id].stats["kills"] += 1
                    hit_player = True
                    break

        # אם לא נגמרים הריבאונדים ולא פגע: שמירה
        if not hit_player and b.bounces <= b.max_bounces:
            new_bullets.append(b)
        else:
            # אם פגע או נגמרו הריבאונדים: מחיקה
            pass

    bullets = new_bullets

    # 3. בדיקת סיום סשן
    alive_players = [p for p in players.values() if p.alive]
    if game_state == "playing" and len(alive_players) <= 1:
        end_session(alive_players[0].id if alive_players else None)


def end_session(winner_id=None):
    """סיום סשן משחק נוכחי"""
    global game_state
    game_state = "session_end"

    if winner_id and winner_id in players:
        players[winner_id].stats["wins"] += 1
        print(f"Session ended. Winner: {players[winner_id].name}")
    else:
        print("Session ended. No winner.")

    # עדכון סטטיסטיקות זמן משחק
    time_elapsed = time.time() - game_start_time
    for p in players.values():
        p.stats["play_time"] += time_elapsed

    # שידור סטטוס סיום
    # אחראי על הלקוח להציג מסך סיום ולחזור ללובי


# --- WebSocket Handlers ---

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    player_id = None
    player_name = None

    try:
        # לולאת טיפול בהודעות
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)

                if data["type"] == "join":
                    # ניהול כניסה ראשונית
                    player_name = data.get("name", f"Tank_{len(players) + 1}")

                    # בדיקת שם פנוי (בפרויקט אמיתי נרצה לעשות בדיקה טובה יותר)
                    if player_name in [p.name for p in players.values()]:
                        player_name += str(random.randint(10, 99))

                    player_color = get_available_color()

                    # יצירת שחקן חדש
                    player_id = str(random.randint(10000, 99999))
                    # בפרויקט אמיתי היינו שולפים סטטיסטיקות ממסד נתונים
                    players[player_id] = Player(player_id, player_name, player_color)

                    # שליחת אישור הצטרפות ללקוח
                    await ws.send_str(json.dumps({
                        "type": "joined",
                        "id": player_id,
                        "color": player_color
                    }))

                    # הוספה ללובי
                    lobby_connections[ws] = player_id
                    await broadcast_lobby_state()
                    print(f"Player {player_name} joined. ID: {player_id}")


                elif player_id in players:
                    p = players[player_id]

                    if data["type"] == "input" and p.alive:
                        # עדכון קלט תנועה וזווית
                        p.move_x = data["dir"]["x"]
                        p.move_y = data["dir"]["y"]
                        # חישוב זווית על בסיס מיקום העכבר/מגע (יותר מדוייק לטנקים)
                        # נניח שהלקוח שולח את הזווית המבוקשת
                        if "angle" in data:
                            p.angle = data["angle"]

                        if data["fire"] and (time.time() - p.last_fire_time > 0.5):
                            # יצירת קליע
                            global last_bullet_id
                            last_bullet_id += 1

                            # יצירת הקליע מחוץ לטנק כדי שלא ייפגע בו
                            bx = p.x + math.cos(p.angle) * (TANK_RADIUS + 5)
                            by = p.y + math.sin(p.angle) * (TANK_RADIUS + 5)

                            bullets.append(Bullet(last_bullet_id, p.id, bx, by, p.angle))
                            p.last_fire_time = time.time()

                    elif data["type"] == "request_start_game":
                        # בקשה להתחיל משחק
                        start_new_game()
                        await broadcast_lobby_state()

                    elif data["type"] == "lobby_reconnect":
                        # אם שחקן מגיע מסשן שהסתיים, שולח בקשה להיות בלובי
                        lobby_connections[ws] = player_id
                        await broadcast_lobby_state()

            elif msg.type == WSMsgType.ERROR:
                print('ws connection closed with exception %s' % ws.exception())

    finally:
        # ניהול יציאה
        if ws in lobby_connections:
            lobby_connections.pop(ws)

        if player_id in players:
            # מחיקת שחקן
            players.pop(player_id)
            print(f"Player {player_name} disconnected. ID: {player_id}")

        await broadcast_lobby_state()

        # אם יש פחות מ 2 שחקנים במשחק, לעצור או לאפס
        if game_state == "playing" and len(players) < MIN_PLAYERS_TO_START:
            print("Game stopped due to insufficient players.")
            end_session(None)


# --- לולאת משחק ראשית (Runs in background) ---

async def game_loop():
    global last_game_update
    while True:
        # חישוב DT
        now = time.time()
        dt = now - last_game_update
        last_game_update = now

        # 30 פעמים בשנייה
        if dt > 0 and game_state != "waiting":
            # עדכון פיזיקה
            update_game_physics(dt * 30)  # מכפיל ב-30 כדי לשמור על מהירות קבועה

            # שידור מצב המשחק לשחקנים הפעילים
            if game_state == "playing":
                game_data = {
                    "type": "game_state",
                    "players": {p_id: p.to_dict() for p_id, p in players.items()},
                    "bullets": [b.to_dict() for b in bullets]
                }

                ws_to_remove = []
                for ws in lobby_connections.keys():
                    if not ws.closed:
                        # שולחים רק לחיבורים שנמצאים בלובי/משחק (כלומר כולם)
                        await ws.send_str(json.dumps(game_data))
                    else:
                        ws_to_remove.append(ws)

                for ws in ws_to_remove:
                    lobby_connections.pop(ws, None)

            # שידור סטטוס לובי גם כשהמשחק פועל (כדי שמשתתפים חדשים ידעו)
            await broadcast_lobby_state()

        await asyncio.sleep(1 / 30)  # 30 עדכונים בשנייה


# --- הגדרות השרת ---
async def init_app():
    app = web.Application()
    app.router.add_get('/', websocket_handler)

    # הפעלת לולאת המשחק ברקע
    asyncio.create_task(game_loop())

    return app


if __name__ == '__main__':
    web.run_app(init_app(), host='0.0.0.0', port=8000)