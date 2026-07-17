"""Development entry point for the local Poppy desktop gateway."""

import argparse
import json

import uvicorn

from .app import create_gateway_app, generate_connection_token


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the local-only Poppy desktop gateway.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)
    token = args.token or generate_connection_token()
    server = None

    def request_shutdown():
        if server is not None:
            server.should_exit = True

    app = create_gateway_app(connection_token=token, shutdown_handler=request_shutdown)
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
    server = uvicorn.Server(config)
    print(json.dumps({"host": "127.0.0.1", "port": args.port, "token": token}), flush=True)
    server.run()


if __name__ == "__main__":
    main()
