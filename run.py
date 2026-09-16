import os

from app import create_app
from app.lifecycle import start_background_workers

app = create_app()
start_background_workers(app)

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug)
