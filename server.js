const WebSocket = require("ws");

const wss = new WebSocket.Server({ port: process.env.PORT || 8765 });

let players = {};
let nextId = 1;

function broadcastState() {
  const msg = JSON.stringify({
    type: "state",
    players: players
  });

  for (const p of Object.values(players)) {
    if (p.ws.readyState === WebSocket.OPEN) {
      p.ws.send(msg);
    }
  }
}

setInterval(() => broadcastState(), 50);

wss.on("connection", (ws) => {
  const id = nextId++;
  players[id] = {
    id,
    name: "Player" + id,
    x: Math.random() * 700 + 50,
    y: Math.random() * 500 + 50,
    angle: 0,
    color: "#" + Math.floor(Math.random() * 16777215).toString(16),
    alive: true,
    ws
  };

  ws.send(JSON.stringify({ type: "joined", id }));

  ws.on("message", (msg) => {
    try {
      const data = JSON.parse(msg);

      if (data.type === "join") {
        players[id].name = data.name;
      }

      if (data.type === "input") {
        players[id].x += data.dir.x * 4;
        players[id].y += data.dir.y * 4;
        players[id].angle = Math.atan2(data.dir.y, data.dir.x);
      }
    } catch (err) {
      console.log("MSG error:", err);
    }
  });

  ws.on("close", () => {
    delete players[id];
  });
});

console.log("Server running...");
