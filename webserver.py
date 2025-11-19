import http.server
import socketserver
import mimetypes

PORT = 8000

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # מאפשר ל-Service Worker להיטען
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def guess_type(self, path):
        if path.endswith("manifest.json"):
            return "application/manifest+json"
        if path.endswith(".js"):
            return "application/javascript"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
    print("Serving at port", PORT)
    httpd.serve_forever()
