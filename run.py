"""Local entrypoint for the Clue Flask app."""

import os

from clue_web import create_app


app = create_app()


class PathPrefixMiddleware:
    """Strip one configured URL prefix so app routes work behind dispatch.yaml."""

    def __init__(self, wsgi_app, prefix: str) -> None:
        """Remember the wrapped app and normalized prefix to strip."""

        self._app = wsgi_app
        self._prefix = "/" + str(prefix or "").strip().strip("/")

    def __call__(self, environ, start_response):
        """Rewrite PATH_INFO/SCRIPT_NAME when the request arrives under the mounted prefix."""

        if self._prefix == "/":
            return self._app(environ, start_response)
        path_info = str(environ.get("PATH_INFO", "") or "")
        if path_info == self._prefix or path_info.startswith(self._prefix + "/"):
            environ["SCRIPT_NAME"] = self._prefix
            new_path = path_info[len(self._prefix) :]
            environ["PATH_INFO"] = new_path if new_path else "/"
        return self._app(environ, start_response)


base_path = str(os.getenv("APP_BASE_PATH", "")).strip()
if base_path:
    app.wsgi_app = PathPrefixMiddleware(app.wsgi_app, base_path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5002, debug=True)
