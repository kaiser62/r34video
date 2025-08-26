# R34Video - TikTok Style Video Browser

A Flask-based TikTok-style video browsing application optimized for Render free tier deployment.

## Features

- 🎥 TikTok-style vertical video browsing
- 🔄 Proxy support with enable/disable option
- 📱 Mobile-responsive design
- ⚡ Optimized for Render free tier
- 🎯 Performance monitoring
- 🔍 Search functionality
- 📊 Resource-conscious caching

## Configuration

### Environment Variables

```bash
USE_PROXY=true              # Enable/disable proxy usage
DEBUG_MODE=false            # Enable debug mode (disable in production)
MAX_WORKERS=2               # Number of worker threads
CACHE_SIZE=128              # LRU cache size
REQUEST_TIMEOUT=8           # HTTP request timeout in seconds
FLASK_ENV=production        # Flask environment
```

### Proxy Configuration

The app uses proxy at `http://192.168.1.140:8887` when `USE_PROXY=true`.

## Deployment

### Vercel (Serverless)

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kaiser62/r34video)

**Quick Setup:**
1. Click the deploy button above or connect your GitHub repository
2. Vercel automatically detects `vercel.json` configuration
3. Environment variables are pre-configured for serverless deployment
4. See [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) for detailed guide

### Render (Traditional Hosting)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

**Quick Setup:**
1. Connect your GitHub repository to Render
2. Use the provided `render.yaml` configuration  
3. Environment variables are pre-configured for free tier
4. See [RENDER_DEPLOYMENT.md](RENDER_DEPLOYMENT.md) for detailed guide

**Production Environment Variables:**
```bash
USE_PROXY=false
DEBUG_MODE=false
MAX_WORKERS=1
CACHE_SIZE=32
REQUEST_TIMEOUT=10
```

### Manual Deployment

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set environment variables:
   ```bash
   export USE_PROXY=true
   export DEBUG_MODE=false
   export FLASK_ENV=production
   ```

3. Run with Gunicorn:
   ```bash
   gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 120 wsgi:app
   ```

## Optimizations for Free Tier

- **Reduced thread pool**: 1-2 workers maximum
- **Memory management**: Periodic garbage collection
- **Request caching**: LRU cache for repeated requests
- **Timeout optimization**: Shorter timeouts to prevent hanging
- **Session pooling**: Reuse HTTP connections
- **Logging optimization**: Minimal logging in production

## Performance Features

- **Performance indicator**: Shows proxy status and loading times
- **Error handling**: User-friendly error messages
- **Resource monitoring**: Request counting and error tracking
- **Adaptive caching**: Configurable cache sizes based on environment

## Usage

1. Browse videos in TikTok-style vertical scroll
2. Click play button to load and play videos
3. Use search functionality to find specific content
4. Performance indicator shows connection status
5. Load more videos with the floating button

## File Structure

```
r34video/
├── app.py              # Main Flask application
├── wsgi.py             # WSGI entry point
├── requirements.txt    # Python dependencies
├── render.yaml         # Render deployment config
├── .env.example        # Environment variables template
└── templates/
    ├── index.html      # Main video feed with search
    └── player.html     # Alternative player view
```

## API Endpoints

- `/` - Main video feed with pagination and search (use `?q=query` for search)
- `/?q=query&page=1` - Search videos (handled by main route)
- `/resolve?url=video_url` - Get video streams and metadata
- `/stream?url=stream_url` - Proxy video streams

## Browser Compatibility

- Modern browsers with ES6+ support
- Mobile Safari and Chrome
- Desktop Chrome, Firefox, Safari, Edge

## Development

For local development:

```bash
export USE_PROXY=true
export DEBUG_MODE=true
python app.py
```

The app will run on `http://localhost:5001`