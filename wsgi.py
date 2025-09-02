import os
import logging
from threading import Thread

# Import app first
from app import app

# Optimize for production
if os.getenv('FLASK_ENV') == 'production':
    app.config['DEBUG'] = False
    app.config['TESTING'] = False
    app.config['PROPAGATE_EXCEPTIONS'] = True

# Configure logging for production
if not app.debug:
    logging.basicConfig(level=logging.WARNING)

# Skip background threads in Vercel serverless environment
if not os.getenv('VERCEL'):
    try:
        from app import background_cleaner
        Thread(target=background_cleaner, daemon=True).start()
    except ImportError:
        pass  # Skip if background_cleaner not available
    
# This is the WSGI callable
application = app

if __name__ == "__main__":
    app.run()
