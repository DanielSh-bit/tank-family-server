const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;

// שרת HTTP בסיסי כדי ש-Render לא יקרוס
const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end("Tank Family WebSocket server is running\n");
});

// שרת WebSocket אמיתי
const wss = new WebSocket.Server({ server: server });

let players = {};

wss.on("connection", (ws) => {
  console.log("Client connected");

  ws.on("message", (msg) => {
    console.log("MSG:", msg.toString());
  });

  ws.on("close", () => {
    console.log("Client disconnected");
  });
});

// הפעלה
server.listen(PORT, () => {
  console.log("Server running on port", PORT);
});
