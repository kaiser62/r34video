import os
import logging
from threading import Thread
from app import app, background_cleaner

# Optimize for production
if os.getenv('FLASK_ENV') == 'production':
    app.config['DEBUG'] = False
    app.config['TESTING'] = False
    app.config['PROPAGATE_EXCEPTIONS'] = True

# Skip background threads in Vercel serverless environment
if not os.getenv('VERCEL'):
    Thread(target=background_cleaner, daemon=True).start()

# Configure logging for production
if not app.debug:
    logging.basicConfig(level=logging.WARNING)
    
# This is the WSGI callable
application = app

if __name__ == "__main__":
    app.run()
