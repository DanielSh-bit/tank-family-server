import asyncio
import json
import math
import random
import time
from aiohttp import web, WSMsgType
import os

# --- הגדרות המשחק ---
TANK_SPEED = 2.0
BULLET_SPEED = 5.0
BULLET_RADIUS = 3
TANK_RADIUS = 15
GAME_WIDTH = 600
GAME_HEIGHT = 600
MIN_PLAYERS_TO_START = 2
USER_DATA_DIR = "users"  # תיקיית שמירת נתוני המשתמש

# רשימת הצבעים הפנויים
AVAILABLE_COLORS = [
    "red", "blue", "green", "pink", "orange", "yellow", "cyan", "magenta"
]

# --- מבני נתונים ---
players = {}  # מזהה שחקן (ID) -> אובייקט Player
lobby_connections = {}  # חיבורי WS (WebSocket)
game_state = "waiting"  # 'waiting', 'playing', 'session_end'
bullets = []
last_bullet_id = 0
last_game_update = time.time()
game_start_time = 0

# 1. רשימה של משתמשים מחוברים כעת (למניעת כניסה כפולה)
# שם משתמש (username) -> מזהה השחקן (player_id)
connected_users = {}


# --- פונקציות לניהול קבצים ושגיאות ---

def ensure_user_data_dir():
    """מוודא שתיקיית שמירת המשתמשים קיימת."""
    if not os.path.exists(USER_DATA_DIR):
        os.makedirs(USER_DATA_DIR)


# טען את התיקייה מיד בהפעלת השרת
ensure_user_data_dir()


def get_user_filepath(username):
    """מחזיר את הנתיב המלא לקובץ המשתמש."""
    return os.path.join(USER_DATA_DIR, f"{username}.json")


# 2. פונקציה לטעינת משתמש
def load_user(username):
    """טוען נתוני משתמש מקובץ JSON."""
    path = get_user_filepath(username)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading user {username}: {e}")
        return None


# 3. פונקציה לשמירת משתמש
def save_user(data):
    """שומר נתוני משתמש לקובץ JSON."""
    username = data["username"]
    path = get_user_filepath(username)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving user {username}: {e}")


async def send_error(ws, error_type, message=None):
    """שולח הודעת שגיאה ללקוח ספציפי."""
    response = {"type": "error", "error": error_type}
    if message:
        response["message"] = message
    await ws.send_str(json.dumps(response))


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
        # שימוש בסטטיסטיקות שנטענו או באתחול
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


# --- פונקציות עזר וניהול משחק ---

def get_available_color():
    """מוצא את הצבע הפנוי הראשון ברשימה."""
    used_colors = {p.color for p in players.values()}
    for color in AVAILABLE_COLORS:
        if color not in used_colors:
            return color
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

    if len(players) < MIN_PLAYERS_TO_START:
        print("Cannot start game: too few players.")
        return

    game_state = "playing"
    bullets = []
    game_start_time = time.time()

    for p in players.values():
        p.alive = True
        p.x = random.randint(TANK_RADIUS, GAME_WIDTH - TANK_RADIUS)
        p.y = random.randint(TANK_RADIUS, GAME_HEIGHT - TANK_RADIUS)
        p.move_x = 0.0
        p.move_y = 0.0

    print("Game started!")


async def broadcast_lobby_state():
    """שולח את מצב הלובי לכל החיבורים הפעילים."""
    state = json.dumps(get_lobby_state())
    disconnected_websockets = []

    for ws in list(lobby_connections.keys()):
        if not ws.closed:
            try:
                await ws.send_str(state)
            except Exception:  # טיפול בשגיאות שליחה/ניתוק פתאומי
                disconnected_websockets.append(ws)
        else:
            disconnected_websockets.append(ws)

    # ניקוי חיבורים שנותקו
    for ws in disconnected_websockets:
        player_id = lobby_connections.pop(ws, None)
        # ניקוי המשתמש המחובר מתבצע ב-finally של websocket_handler, אבל נבצע כאן ניקוי של ה-WS
        if player_id and player_id in players:
            print(f"Cleaning up stuck lobby WS for player {players[player_id].name}")


def end_session(winner_id=None):
    """סיום סשן משחק נוכחי ושמירת סטטיסטיקות."""
    global game_state

    if game_state != "playing":
        return

    game_state = "session_end"

    time_elapsed = time.time() - game_start_time
    time_elapsed = max(0, time_elapsed)  # ודא זמן חיובי

    # שמירה ועדכון סטטיסטיקות
    for p_id, p in players.items():
        if p_id == winner_id:
            p.stats["wins"] += 1

        p.stats["play_time"] += time_elapsed

        # מציאת שם המשתמש לשמירה
        username_to_save = None
        for username, pid in connected_users.items():
            if pid == p_id:
                username_to_save = username
                break

        if username_to_save:
            # טעינת נתוני המשתמש (כדי לוודא ששאר השדות נשמרים)
            user_data = load_user(username_to_save)
            if user_data:
                # עדכון הסטטיסטיקות
                user_data["kills"] = p.stats["kills"]
                user_data["wins"] = p.stats["wins"]
                user_data["play_time"] = p.stats["play_time"]
                save_user(user_data)
                print(f"Saved stats for user: {username_to_save}")
            else:
                print(f"Error: Could not find user file for connected player {username_to_save} to save stats.")

    if winner_id and winner_id in players:
        print(f"Session ended. Winner: {players[winner_id].name}")
    else:
        print("Session ended. No winner found or game stopped.")

    asyncio.create_task(broadcast_lobby_state())


# --- לוגיקת משחק ---

def update_game_physics(dt):
    """עדכון מיקומי שחקנים וקליעים."""
    global bullets

    # 1. עדכון שחקנים (בדיקת גבולות)
    for p in players.values():
        if p.alive:
            # נרמול וקטור התנועה
            move_magnitude = math.sqrt(p.move_x ** 2 + p.move_y ** 2)
            if move_magnitude > 1.0:
                norm_x = p.move_x / move_magnitude
                norm_y = p.move_y / move_magnitude
            else:
                norm_x = p.move_x
                norm_y = p.move_y

            effective_dt = min(dt, 0.1)
            new_x = p.x + norm_x * TANK_SPEED * effective_dt
            new_y = p.y + norm_y * TANK_SPEED * effective_dt

            # בדיקת גבולות
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
            b.x = BULLET_RADIUS
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
                    if b.owner_id in players:
                        players[b.owner_id].stats["kills"] += 1
                    hit_player = True
                    break

        if not hit_player and b.bounces <= b.max_bounces:
            new_bullets.append(b)

    bullets = new_bullets

    # 3. בדיקת סיום סשן
    alive_players = [p for p in players.values() if p.alive]
    if game_state == "playing" and len(alive_players) <= 1:
        winner_id = alive_players[0].id if alive_players else None
        end_session(winner_id)


# --- לוגיקות כניסה והרשמה ---

async def handle_register(ws, data):
    """מטפל בבקשת הרשמה (יצירת משתמש חדש)."""
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        await send_error(ws, "invalid_fields", "שם משתמש או סיסמה ריקים.")
        return

    # בדיקה האם המשתמש כבר קיים
    if os.path.exists(get_user_filepath(username)):
        await send_error(ws, "user_exists", "משתמש בשם זה כבר קיים.")
        return

    # יצירת נתונים התחלתיים
    initial_stats = {"kills": 0, "wins": 0, "play_time": 0}
    user_data = {
        "username": username,
        "password": password,  # *** שימו לב: סיסמה נשמרת בטקסט רגיל (לא מומלץ באפליקציות אמיתיות!) ***
        **initial_stats
    }

    # שמירת המשתמש
    save_user(user_data)

    print(f"User registered: {username}")
    await ws.send_str(json.dumps({"type": "register_ok", "username": username}))


async def handle_login(ws, data):
    """מטפל בבקשת כניסה (אימות משתמש קיים)."""
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        await send_error(ws, "invalid_fields", "שם משתמש או סיסמה ריקים.")
        return

    # בדיקה אם המשתמש כבר מחובר (חסימת כניסה כפולה)
    if username in connected_users:
        print(f"Login rejected: {username} already connected.")
        await send_error(ws, "already_connected", "משתמש זה כבר מחובר.")
        return

    # טעינת המשתמש
    user_data = load_user(username)
    if not user_data:
        await send_error(ws, "user_not_found", "שם משתמש לא נמצא.")
        return

    # בדיקת סיסמה
    if user_data["password"] != password:
        await send_error(ws, "wrong_password", "סיסמה שגויה.")
        return

    # כניסה מוצלחת
    # נשלח בחזרה את הסטטיסטיקות
    stats = {
        "kills": user_data.get("kills", 0),
        "wins": user_data.get("wins", 0),
        "play_time": user_data.get("play_time", 0)
    }

    print(f"User logged in: {username}")
    await ws.send_str(json.dumps({
        "type": "login_ok",
        "username": username,
        "stats": stats
    }))

    # בשלב זה, החיבור עדיין לא נחשב 'שחקן' במשחק, רק 'מחובר'.
    # הוספת ה-username ל-WS לצורך ניתוק נקי ב-finally
    ws['username'] = username


async def handle_join(ws, data, username, stats):
    """מטפל בבקשת הצטרפות ללובי לאחר כניסה מוצלחת."""

    # יצירת מזהה ייחודי לשחקן
    player_id = str(random.randint(10000, 99999))
    while player_id in players:
        player_id = str(random.randint(10000, 99999))

    player_color = get_available_color()

    # יצירת אובייקט שחקן עם הסטטיסטיקות שנטענו
    players[player_id] = Player(player_id, username, player_color, initial_stats=stats)

    # הוספה לרשימת המשתמשים המחוברים והקישור ל-Player ID
    connected_users[username] = player_id

    # שליחת אישור הצטרפות ללקוח הספציפי
    await ws.send_str(json.dumps({
        "type": "joined",
        "id": player_id,
        "color": player_color,
        "name": username,
        "stats": stats  # שליחת הסטטיסטיקות שוב
    }))

    # הוספה לרשימת חיבורי הלובי
    lobby_connections[ws] = player_id

    # הוספת ה-player_id ל-WS לצורך ניתוק נקי ב-finally
    ws['player_id'] = player_id

    await broadcast_lobby_state()
    print(f"Player {username} joined lobby. ID: {player_id}")
    return player_id


# --- WebSocket Handlers ---

async def websocket_handler(request):
    """מטפל בחיבורי WebSocket ובקבלת פקודות מהלקוחות."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    player_id = None
    username = None
    user_stats = None

    try:
        # לולאת טיפול בהודעות
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    print("Received invalid JSON data.")
                    continue

                # --- טיפול בהודעות כניסה/הרשמה ---
                if "type" not in data:
                    continue

                if data["type"] == "register":
                    await handle_register(ws, data)

                elif data["type"] == "login":
                    await handle_login(ws, data)

                # --- טיפול ב-JOIN לאחר אישור login ---
                elif data["type"] == "join":
                    # בדיקה אם קיבלנו אישור login_ok ושם המשתמש נשמר
                    if 'username' in ws:
                        username = ws['username']
                        # אנחנו צריכים את הסטטיסטיקות מתוך קובץ המשתמש
                        user_data = load_user(username)
                        user_stats = {
                            "kills": user_data.get("kills", 0),
                            "wins": user_data.get("wins", 0),
                            "play_time": user_data.get("play_time", 0)
                        }

                        # הוספת השחקן ללובי
                        player_id = await handle_join(ws, data, username, user_stats)
                    else:
                        # ניסיון לעשות join בלי login
                        await send_error(ws, "auth_required", "יש להתחבר לפני הצטרפות למשחק.")


                # --- טיפול בקלט משחק לאחר הצטרפות ---
                elif player_id in players:
                    p = players[player_id]

                    if data["type"] == "input":
                        # קבלת קלט משחק
                        if p.alive:
                            p.move_x = data["dir"].get("x", 0.0)
                            p.move_y = data["dir"].get("y", 0.0)
                            if "angle" in data:
                                p.angle = data["angle"]

                            # בדיקת ירי
                            if data.get("fire") and (time.time() - p.last_fire_time > 0.5):
                                global last_bullet_id
                                last_bullet_id += 1

                                offset_distance = TANK_RADIUS + 5
                                bx = p.x + math.cos(p.angle) * offset_distance
                                by = p.y + math.sin(p.angle) * offset_distance

                                bullets.append(Bullet(last_bullet_id, p.id, bx, by, p.angle))
                                p.last_fire_time = time.time()

                    elif data["type"] == "request_start_game":
                        start_new_game()
                        await broadcast_lobby_state()

                    elif data["type"] == "lobby_reconnect":
                        # חזרה ללובי מסיום סשן (החיבור נשאר פעיל)
                        lobby_connections[ws] = player_id
                        await broadcast_lobby_state()

            elif msg.type == WSMsgType.ERROR:
                print('ws connection closed with exception %s' % ws.exception())

            elif msg.type == WSMsgType.CLOSE:
                break

    finally:
        # --- ניהול יציאה/התנתקות ---

        # 1. ניקוי החיבור מרשימת הלובי/שידור
        lobby_connections.pop(ws, None)

        # 2. אם השחקן הצטרף (join) יש לנקות את הנתונים שלו
        current_username = ws.get('username')
        current_player_id = ws.get('player_id')

        if current_username in connected_users:
            # מחיקת השחקן מרשימת המשתמשים המחוברים (מאפשר כניסה חוזרת)
            connected_users.pop(current_username)
            print(f"User {current_username} disconnected and session cleared.")

        if current_player_id in players:
            # מחיקת אובייקט השחקן מהמשחק
            players.pop(current_player_id)
            print(f"Player object {current_player_id} removed.")

        # אם המשחק פועל ומספר השחקנים ירד מתחת למינימום
        if game_state == "playing" and len(players) < MIN_PLAYERS_TO_START:
            print("Game stopped due to insufficient players.")
            end_session(None)

        # עדכון הלובי לאחר ניתוק
        asyncio.create_task(broadcast_lobby_state())

    return ws


# --- לולאת משחק ראשית (פועלת ברקע) ---

async def game_loop():
    """הלולאה הרצה של המשחק המטפלת בפיזיקה ושידור מצב המשחק."""
    global last_game_update
    UPDATE_RATE = 30
    FRAME_TIME = 1 / UPDATE_RATE

    while True:
        await asyncio.sleep(FRAME_TIME)

        now = time.time()
        dt = now - last_game_update
        last_game_update = now

        if dt > 0 and game_state == "playing":

            update_game_physics(dt)

            game_data = {
                "type": "game_state",
                "players": {p_id: p.to_dict() for p_id, p in players.items()},
                "bullets": [b.to_dict() for b in bullets],
                "game_state": game_state
            }

            message_to_send = json.dumps(game_data)

            ws_to_remove = []
            for ws in list(lobby_connections.keys()):
                if not ws.closed:
                    try:
                        await ws.send_str(message_to_send)
                    except Exception:
                        ws_to_remove.append(ws)
                else:
                    ws_to_remove.append(ws)

            for ws in ws_to_remove:
                lobby_connections.pop(ws, None)

        elif game_state == "session_end" or game_state == "waiting":
            # שידור מצב לובי (עבור שחקנים שמחכים או סיימו משחק)
            asyncio.create_task(broadcast_lobby_state())


# --- הגדרות השרת ---
async def init_app():
    """מאתחל את יישום ה-aiohttp ומגדיר את הניתובים."""
    app = web.Application()
    app.router.add_get('/ws', websocket_handler)

    # הפעלת לולאת המשחק ברקע
    asyncio.create_task(game_loop())

    return app


if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 8000))
    print(f"Starting Tank Battle Server on ws://0.0.0.0:{PORT}")
    web.run_app(init_app(), host='0.0.0.0', port=PORT)