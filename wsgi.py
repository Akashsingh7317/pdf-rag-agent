"""Entry point.

Local dev:   python wsgi.py
Production:  gunicorn -w 2 -k gthread --threads 8 -b 0.0.0.0:8000 wsgi:app
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
        threaded=True,
    )
