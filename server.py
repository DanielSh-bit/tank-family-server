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
    """מייצג שחקן (טנק) במשחק."""

    def __init__(self, player_id, name, color, initial_stats=None):
        self.id = player_id
        self.name = name
        self.color = color
        # מיקום התחלתי אקראי
        self.x = random.randint(TANK_RADIUS, GAME_WIDTH - TANK_RADIUS)
        self.y = random.randint(TANK_RADIUS, GAME_HEIGHT - TANK_RADIUS)
        self.angle = 0.0  # זווית הסיבוב של הטנק
        self.alive = True
        self.stats = initial_stats or {"kills": 0, "wins": 0, "play_time": 0}
        self.last_fire_time = 0  # זמן ירייה אחרון (למניעת ירי רצוף)
        self.move_x = 0.0  # רכיב תנועה X (מ-1- עד 1)
        self.move_y = 0.0  # רכיב תנועה Y (מ-1- עד 1)

    def to_dict(self):
        """מחזיר מילון עם הנתונים הציבוריים של השחקן."""
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
    """מייצג קליע במשחק."""

    def __init__(self, bullet_id, owner_id, x, y, angle):
        self.id = bullet_id
        self.owner_id = owner_id
        self.x = x
        self.y = y
        self.angle = angle
        # חישוב וקטור מהירות
        self.vx = math.cos(angle) * BULLET_SPEED
        self.vy = math.sin(angle) * BULLET_SPEED
        self.bounces = 0
        self.max_bounces = 1  # הגבלת ריבאונד (ניתור)

    def to_dict(self):
        """מחזיר מילון עם נתוני הקליע לשידור."""
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "x": self.x,
            "y": self.y,
        }


# --- פונקציות עזר ---

def get_available_color():
    """מוצא את הצבע הפנוי הראשון ברשימה."""
    used_colors = {p.color for p in players.values()}
    for color in AVAILABLE_COLORS:
        if color not in used_colors:
            return color
    # אם אין צבעים פנויים, מחזיר צבע שכבר בשימוש.
    return AVAILABLE_COLORS[0]


def get_lobby_state():
    """מחזיר את הסטטוס הנוכחי של הלובי."""
    num_players = len(players)
    return {
        "type": "lobby_state",
        "game_state": game_state,
        "num_players": num_players,
        "players": {p_id: p.to_dict() for p_id, p in players.items()}
    }


def start_new_game():
    """מאפס את המצב ומתחיל משחק חדש."""
    global game_state, bullets, game_start_time

    # בדיקה האם יש מספיק שחקנים להתחיל משחק
    if len(players) < MIN_PLAYERS_TO_START:
        print("Cannot start game: too few players.")
        return

    game_state = "playing"
    bullets = []
    game_start_time = time.time()

    # מיקום טנקים מחדש ומחיאתם לתחילת סשן חדש
    for p in players.values():
        p.alive = True
        p.x = random.randint(TANK_RADIUS, GAME_WIDTH - TANK_RADIUS)
        p.y = random.randint(TANK_RADIUS, GAME_HEIGHT - TANK_RADIUS)
        p.move_x = 0.0
        p.move_y = 0.0

    print("Game started!")


async def broadcast_lobby_state():
    """שולח את מצב הלובי לכל החיבורים הפעילים בלובי."""
    state = json.dumps(get_lobby_state())
    disconnected_websockets = []

    # שליחה לכל חיבור WS שנרשם ב-lobby_connections
    for ws in lobby_connections.keys():
        if not ws.closed:
            try:
                await ws.send_str(state)
            except ConnectionResetError:
                disconnected_websockets.append(ws)
        else:
            disconnected_websockets.append(ws)

    # ניקוי חיבורים שנותקו
    for ws in disconnected_websockets:
        lobby_connections.pop(ws, None)


# --- לוגיקת משחק ---

def update_game_physics(dt):
    """
    עדכון מיקומי שחקנים וקליעים
    dt: דלתא זמן (זמן שעבר מאז העדכון האחרון).
    """
    global bullets

    # 1. עדכון שחקנים (בדיקת גבולות)
    for p in players.values():
        if p.alive:
            # נרמול וקטור התנועה למניעת תנועה מהירה באלכסון
            move_magnitude = math.sqrt(p.move_x ** 2 + p.move_y ** 2)
            if move_magnitude > 1.0:
                norm_x = p.move_x / move_magnitude
                norm_y = p.move_y / move_magnitude
            else:
                norm_x = p.move_x
                norm_y = p.move_y

            new_x = p.x + norm_x * TANK_SPEED * dt
            new_y = p.y + norm_y * TANK_SPEED * dt

            # בדיקת גבולות ומניעת יציאה
            p.x = max(TANK_RADIUS, min(new_x, GAME_WIDTH - TANK_RADIUS))
            p.y = max(TANK_RADIUS, min(new_y, GAME_HEIGHT - TANK_RADIUS))

    # 2. עדכון קליעים (ובדיקת גבולות/פגיעה)
    new_bullets = []
    for b in bullets:
        # תנועה
        b.x += b.vx * dt
        b.y += b.vy * dt

        # בדיקת גבולות העולם (ריבאונד)
        hit_wall = False
        if b.x - BULLET_RADIUS < 0:
            b.x = BULLET_RADIUS  # מיקום מחדש בתוך הגבול
            b.vx *= -1
            hit_wall = True
        elif b.x + BULLET_RADIUS > GAME_WIDTH:
            b.x = GAME_WIDTH - BULLET_RADIUS
            b.vx *= -1
            hit_wall = True

        if b.y - BULLET_RADIUS < 0:
            b.y = BULLET_RADIUS
            b.vy *= -1
            hit_wall = True
        elif b.y + BULLET_RADIUS > GAME_HEIGHT:
            b.y = GAME_HEIGHT - BULLET_RADIUS
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
                    # עדכון סטטיסטיקת הריגות
                    if b.owner_id in players:
                        players[b.owner_id].stats["kills"] += 1
                    hit_player = True
                    break

        # אם לא נגמרים הריבאונדים ולא פגע: שמירה ברשימה החדשה
        if not hit_player and b.bounces <= b.max_bounces:
            new_bullets.append(b)
        # אחרת, הקליע נמחק

    bullets = new_bullets

    # 3. בדיקת סיום סשן
    alive_players = [p for p in players.values() if p.alive]

    # המשחק מסתיים כאשר יש שחקן אחד או פחות חיים
    if game_state == "playing" and len(alive_players) <= 1:
        winner_id = alive_players[0].id if alive_players else None
        end_session(winner_id)


def end_session(winner_id=None):
    """סיום סשן משחק נוכחי."""
    global game_state
    game_state = "session_end"

    # עדכון סטטיסטיקות ניצחון
    if winner_id and winner_id in players:
        players[winner_id].stats["wins"] += 1
        print(f"Session ended. Winner: {players[winner_id].name}")
    else:
        print("Session ended. No winner found.")

    # עדכון סטטיסטיקות זמן משחק
    time_elapsed = time.time() - game_start_time
    for p in players.values():
        # לוודא שזמן הריצה חיובי
        p.stats["play_time"] += max(0, time_elapsed)

    # שידור סטטוס סיום לכל הלקוחות
    asyncio.create_task(broadcast_lobby_state())


# --- WebSocket Handlers ---

async def websocket_handler(request):
    """מטפל בחיבורי WebSocket ובקבלת פקודות מהלקוחות."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    player_id = None
    player_name = None

    try:
        # לולאת טיפול בהודעות
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    print("Received invalid JSON data.")
                    continue

                if data["type"] == "join":
                    # --- כניסה ראשונית ---
                    player_name = data.get("name", f"Tank_{random.randint(100, 999)}")

                    # ודא ייחודיות לשם
                    temp_name = player_name
                    i = 1
                    while temp_name in [p.name for p in players.values()]:
                        temp_name = f"{player_name}_{i}"
                        i += 1
                    player_name = temp_name

                    player_color = get_available_color()

                    # יצירת מזהה ייחודי לשחקן
                    player_id = str(random.randint(10000, 99999))
                    while player_id in players:
                        player_id = str(random.randint(10000, 99999))

                    # יצירת אובייקט שחקן
                    # סטטיסטיקות התחלתיות אמיתיות היו נשלפות מכאן ממסד נתונים
                    players[player_id] = Player(player_id, player_name, player_color)

                    # שליחת אישור הצטרפות ללקוח הספציפי
                    await ws.send_str(json.dumps({
                        "type": "joined",
                        "id": player_id,
                        "color": player_color
                    }))

                    # הוספה לרשימת חיבורי הלובי
                    lobby_connections[ws] = player_id
                    await broadcast_lobby_state()
                    print(f"Player {player_name} joined. ID: {player_id}")


                elif player_id in players:
                    p = players[player_id]

                    if data["type"] == "input":
                        # --- קבלת קלט משחק ---
                        if p.alive:
                            # עדכון תנועה (X, Y)
                            p.move_x = data["dir"].get("x", 0.0)
                            p.move_y = data["dir"].get("y", 0.0)

                            # עדכון זווית
                            if "angle" in data:
                                p.angle = data["angle"]

                            # בדיקת ירי
                            if data.get("fire") and (time.time() - p.last_fire_time > 0.5):
                                # יצירת קליע חדש
                                global last_bullet_id
                                last_bullet_id += 1

                                # יצירת הקליע במרחק קטן מהטנק לכיוון הירי
                                offset_distance = TANK_RADIUS + 5
                                bx = p.x + math.cos(p.angle) * offset_distance
                                by = p.y + math.sin(p.angle) * offset_distance

                                bullets.append(Bullet(last_bullet_id, p.id, bx, by, p.angle))
                                p.last_fire_time = time.time()

                    elif data["type"] == "request_start_game":
                        # --- בקשה להתחיל משחק ---
                        start_new_game()
                        # שידור סטטוס לובי מעודכן (כדי להכריז על 'playing')
                        await broadcast_lobby_state()

                    elif data["type"] == "lobby_reconnect":
                        # --- חזרה ללובי מסיום סשן ---
                        lobby_connections[ws] = player_id
                        await broadcast_lobby_state()

            elif msg.type == WSMsgType.ERROR:
                print('ws connection closed with exception %s' % ws.exception())

            elif msg.type == WSMsgType.CLOSE:
                break

    finally:
        # --- ניהול יציאה/התנתקות ---
        if ws in lobby_connections:
            lobby_connections.pop(ws)

        if player_id in players:
            # מחיקת השחקן מהמשחק
            players.pop(player_id)
            print(f"Player {player_name} disconnected. ID: {player_id}")

        # אם המשחק פועל ומספר השחקנים ירד מתחת למינימום
        if game_state == "playing" and len(players) < MIN_PLAYERS_TO_START:
            print("Game stopped due to insufficient players.")
            end_session(None)

        # עדכון הלובי לאחר ניתוק
        await broadcast_lobby_state()


# --- לולאת משחק ראשית (פועלת ברקע) ---

async def game_loop():
    """הלולאה הרצה של המשחק המטפלת בפיזיקה ושידור מצב המשחק."""
    global last_game_update
    # קצב עדכון רצוי: 30 פעמים בשנייה
    UPDATE_RATE = 30
    FRAME_TIME = 1 / UPDATE_RATE

    while True:
        await asyncio.sleep(FRAME_TIME)

        now = time.time()
        # dt הוא הזמן האמיתי שעבר
        dt = now - last_game_update
        last_game_update = now

        # אם עבר זמן סביר (הגנה מפני קריסה)
        if dt > 0 and game_state != "waiting":

            # עדכון פיזיקה. אנו מעבירים את ה-dt האמיתי לעדכון.
            update_game_physics(dt)

            # שידור מצב המשחק לשחקנים הפעילים
            if game_state == "playing" or game_state == "session_end":
                game_data = {
                    "type": "game_state",
                    "players": {p_id: p.to_dict() for p_id, p in players.items()},
                    "bullets": [b.to_dict() for b in bullets],
                    "game_state": game_state  # חשוב לשדר גם את סטטוס הסיום
                }

                message_to_send = json.dumps(game_data)

                ws_to_remove = []
                for ws in list(lobby_connections.keys()):  # עבודה על עותק של המפתחות
                    if not ws.closed:
                        try:
                            # שולחים לכל החיבורים הפעילים
                            await ws.send_str(message_to_send)
                        except Exception:  # טיפול בשגיאות שליחה
                            ws_to_remove.append(ws)
                    else:
                        ws_to_remove.append(ws)

                for ws in ws_to_remove:
                    lobby_connections.pop(ws, None)

            # שידור סטטוס לובי (גם כשהמשחק פועל, לשחקנים חדשים או חוזרים)
            if game_state != "playing":
                await broadcast_lobby_state()


# --- הגדרות השרת ---
async def init_app():
    """מאתחל את יישום ה-aiohttp ומגדיר את הניתובים."""
    app = web.Application()
    # הניתוב הבסיסי לטיפול ב-WebSocket
    app.router.add_get('/', websocket_handler)

    # הפעלת לולאת המשחק ברקע
    asyncio.create_task(game_loop())

    return app


if __name__ == '__main__':
    # מריץ את השרת על פורט 8000
    print("Starting Tank Battle Server on ws://0.0.0.0:8000")
    web.run_app(init_app(), host='0.0.0.0', port=8000)