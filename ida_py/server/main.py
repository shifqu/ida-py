"""Ida's HTTP server main functionality."""
import json
import re
import signal
import socketserver
import traceback
from datetime import datetime, timezone
from pathlib import Path
from socket import SHUT_WR, socket
from typing import Callable, NoReturn
from urllib.parse import parse_qs, urlparse

from ida_py.server.errors import ApiException
from ida_py.server.models import Request, Response

Route = tuple[re.Pattern, Callable]


class ApplicationServer(socketserver.TCPServer):
    """Represent a TCPServer that supports routes and whose address can be reused."""

    allow_reuse_address = True

    def __init__(self, *args, routes: list[Route] | None = None, **kwargs) -> None:
        self.routes = routes or []
        super().__init__(*args, **kwargs)


class TCPHandler(socketserver.BaseRequestHandler):
    """Represent a TCPHandler which handles request and ensures the tcp_socket is shutdown."""

    def handle(self):
        """Handle the tcp request."""
        tcp_socket: socket = self.request
        request = tcp_socket.recv(4096).decode()
        # self._write_to_file(request, "request")
        try:
            parsed_req = self._parse_request(request)
            response = self._get_route_function(parsed_req.path)(parsed_req)
        except ApiException as e:
            response = e
        except Exception:
            print(traceback.format_exc())
            response = ApiException({"ok": False}, 500)
        response = self._build_response(response).encode()
        tcp_socket.sendall(response)

    def finish(self):
        """Try to shutdown the tcp_socket in a safe way."""
        tcp_socket: socket = self.request
        try:
            tcp_socket.shutdown(SHUT_WR)
        except OSError:
            """Happens when the transport endpoint is not connected."""

    def _build_response(self, response: Response) -> str:
        headers = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
        if headers:
            headers += "\n"  # Append a new-line in case additional headers are present.
        body = response.body if isinstance(response.body, str) else json.dumps(response.body)
        response_str = (
            f"HTTP/1.1 {response.status_code}\n"
            f"Content-Type: {response.content_type}; charset=utf-8\n"
            f"Content-Length: {len(body)}\n"
            "Connection: close\n"
            f"{headers}"
            f"\n"
            f"{body}"
        )
        # self._write_to_file(response_str, "response")
        return response_str

    def _get_route_function(self, searched_path: str) -> Callable:
        if not isinstance(self.server, ApplicationServer):
            raise TypeError("This TCPHandler only supports ApplicationServer instances.")

        return next(r for r in self.server.routes if r[0].match(searched_path))[1]

    def _parse_request(self, request_str: str) -> Request:
        request_lines = request_str.splitlines()
        if not request_lines:
            raise TypeError("Malformed request.")  # This is likely a connect attempt
        method, url_str, _ = request_lines.pop(0).split(" ")
        headers = {}
        for i, line in enumerate(request_lines):
            if line == "":  # under empty line, whole data is body
                body = "".join(request_lines[i + 1 :])
                break
            key, value = (x.strip() for x in line.split(":", 1))
            uppercase_key = key.upper()
            headers[uppercase_key] = value
        url = urlparse(url_str)
        return Request(method, headers, url.path, parse_qs(url.query), body)

    def _write_to_file(self, text: str, name: str) -> None:
        now_timestamp = int(datetime.now(tz=timezone.utc).timestamp())
        parent_parent = Path(__file__).parent.parent
        root = parent_parent / "tests" / "data" / "server" / str(now_timestamp)
        root.mkdir(exist_ok=True, parents=True)
        filepath = root / f"{name}.txt"
        filepath.write_text(text)


class Application:
    """Represent the main Application."""

    routes: list[Route] = []

    def __init__(self):
        """Shutdown on SIGTERM signal."""
        signal.signal(signal.SIGTERM, self.shutdown)

    def route(self, path: str) -> Callable:
        """Decorate the given function and listen on the given path once the server is started."""

        def _decorator(func: Callable):
            def _inner():
                return func()

            self.routes.append((re.compile(path + "$"), func))
            return _inner

        return _decorator

    def serve(self, host: str, port: int):
        """Activate the server.

        This will keep running until you interrupt the program with Ctrl-C.
        The SIGTERM signal will also interrupt the program.

        Parameters
        ----------
        host : str
            The host on which we will listen for requests.
        port : int
            The port on which we will listen for requests.
        """
        with ApplicationServer((host, port), TCPHandler, routes=self.routes) as server:
            server.serve_forever()

    @staticmethod
    def shutdown(*_) -> NoReturn:
        """Shutdown by raising a KeyboardInterrupt."""
        raise KeyboardInterrupt()