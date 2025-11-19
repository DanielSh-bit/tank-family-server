const functions = require('firebase-functions');
const WebSocket = require('ws');

// יוצרים שרת WebSocket
exports.ws = functions.https.onRequest((req, res) => {
    const wss = new WebSocket.Server({ noServer: true });

    if (req.headers['upgrade'] !== 'websocket') {
        return res.status(400).send("Expected websocket");
    }

    req.socket.server.on('upgrade', (request, socket, head) => {
        wss.handleUpgrade(request, socket, head, (ws) => {
            wss.emit('connection', ws, request);
        });
    });

    wss.on('connection', (ws) => {
        console.log('Client connected');

        ws.send(JSON.stringify({ msg: "Welcome to Tank Family server!" }));

        ws.on('message', data => {
            console.log("Got:", data.toString());
            ws.send("Echo: " + data.toString());
        });

        ws.on('close', () => {
            console.log('Client disconnected');
        });
    });
});
