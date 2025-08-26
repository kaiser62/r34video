import os
from app import app

# Optimize for production
if os.getenv('FLASK_ENV') == 'production':
    app.config['DEBUG'] = False
    app.config['TESTING'] = False

if __name__ == "__main__":
    app.run()
