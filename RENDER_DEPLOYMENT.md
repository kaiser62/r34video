# Render Deployment Guide

Complete guide for deploying R34Video TikTok Style App on Render's free tier.

## 🚀 Quick Deploy

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

## 📋 Prerequisites

1. **GitHub Account**: Your code repository
2. **Render Account**: Free account at [render.com](https://render.com)
3. **Repository Access**: Render connected to your GitHub

## 🔧 Deployment Steps

### 1. Connect Repository
1. Log into [Render Dashboard](https://dashboard.render.com)
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Select the repository containing this code

### 2. Configure Service
Use these exact settings:

**Basic Configuration:**
- **Name**: `r34video-app`
- **Environment**: `Python`
- **Runtime**: `Python 3.11` (recommended)
- **Region**: Choose closest to your users
- **Branch**: `main`

**Build & Deploy:**
- **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
- **Start Command**: `gunicorn --bind 0.0.0.0:$PORT --workers 1 --worker-class sync --timeout 120 --keep-alive 30 --max-requests 500 --max-requests-jitter 50 --preload wsgi:app`

**Service Details:**
- **Plan**: `Free` (0 instances when idle)
- **Health Check Path**: `/health`
- **Auto Deploy**: `Disabled` (manual deploys recommended)

### 3. Environment Variables
Set these in Render Dashboard → Environment:

```bash
USE_PROXY=false
DEBUG_MODE=false
MAX_WORKERS=1
CACHE_SIZE=32
REQUEST_TIMEOUT=10
FLASK_ENV=production
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
WEB_CONCURRENCY=1
```

### 4. Advanced Settings
- **Health Check Path**: `/health`
- **Auto Deploy**: Disabled (for controlled deployments)
- **Docker Command**: Leave blank (using buildpack)

## 🎯 Render-Specific Optimizations

### Free Tier Limitations
- **Sleep after 15min**: Service sleeps when inactive
- **750 hours/month**: Total runtime limit
- **512MB RAM**: Memory constraint
- **Single CPU**: Processing limitation
- **Cold starts**: ~30s startup time

### Our Optimizations
✅ **Single Worker**: Minimizes memory usage  
✅ **Small Cache**: 32-item LRU cache  
✅ **No Proxy**: Disabled for cloud deployment  
✅ **Optimized Timeouts**: 10s request timeout  
✅ **Memory Management**: Periodic garbage collection  
✅ **Health Checks**: Built-in `/health` endpoint  

## 📊 Performance Expectations

### Cold Start Performance
- **First Request**: ~30-45 seconds (service waking up)
- **Subsequent Requests**: ~1-3 seconds
- **Video Loading**: ~2-5 seconds (depending on external site)

### Free Tier Usage Tips
1. **Avoid Peak Hours**: Deploy during low-traffic times
2. **Monitor Usage**: Check dashboard for hours consumed
3. **Optimize Requests**: Use caching effectively
4. **Health Monitoring**: Service auto-sleeps after 15min idle

## 🛠️ Troubleshooting

### Common Issues

**Build Failures:**
```bash
# Check build logs for dependency issues
pip install --upgrade pip
pip install -r requirements.txt
```

**Service Won't Start:**
```bash
# Verify WSGI configuration
gunicorn wsgi:app
```

**Health Check Failing:**
```bash
# Test health endpoint locally
curl https://your-app.onrender.com/health
```

**Memory Issues:**
```bash
# Reduce cache size if needed
CACHE_SIZE=16
MAX_WORKERS=1
```

### Log Analysis
1. **Build Logs**: Check dependency installation
2. **Deploy Logs**: Verify service startup
3. **Service Logs**: Monitor runtime errors
4. **Metrics**: CPU/Memory usage patterns

## 🔍 Monitoring

### Built-in Monitoring
- **Health Checks**: Automatic via `/health` endpoint
- **Service Logs**: Real-time in Render dashboard
- **Metrics**: CPU, Memory, Request count
- **Alerts**: Optional email notifications

### Custom Monitoring
The app includes:
- **Performance Tracking**: Response times and error rates
- **Resource Usage**: Memory and request patterns
- **Debug Information**: Optional on-page debugging

## 🔄 Updates & Maintenance

### Manual Deployment
1. Push changes to GitHub
2. Go to Render Dashboard
3. Click "Manual Deploy" → "Deploy latest commit"
4. Monitor deployment logs

### Automated Deployment
```yaml
# In render.yaml
autoDeploy: true  # Enable auto-deploy on git push
```

### Rolling Back
1. Go to "Deploys" tab in Render
2. Find previous successful deployment
3. Click "Redeploy"

## 🚨 Production Checklist

Before deploying to production:

- [ ] Environment variables configured
- [ ] DEBUG_MODE=false
- [ ] USE_PROXY=false (for cloud deployment)
- [ ] Health checks enabled
- [ ] Build command tested locally
- [ ] WSGI configuration validated
- [ ] Resource limits appropriate
- [ ] Monitoring enabled
- [ ] Backup deployment strategy

## 📈 Scaling Options

### Free Tier Limits
- **1 Service**: Single web service only
- **750 Hours**: Monthly runtime limit
- **No Persistent Storage**: Files don't persist between deploys

### Upgrade Options
- **Starter Plan** ($7/month): No sleep, more resources
- **Standard Plan** ($25/month): Multiple services, databases
- **Pro Plan** ($85/month): High performance, priority support

## 🔗 Useful Links

- **Render Docs**: https://render.com/docs
- **Python Guide**: https://render.com/docs/deploy-flask
- **Dashboard**: https://dashboard.render.com
- **Status Page**: https://status.render.com

## 💡 Pro Tips

1. **Test Locally First**: Always test with production environment variables
2. **Monitor Resource Usage**: Keep an eye on memory and CPU usage
3. **Use Health Checks**: Helps with automatic recovery
4. **Optimize Images**: Smaller Docker images deploy faster
5. **Cache Effectively**: Reduce external API calls

---

## 🆘 Support

If you encounter issues:
1. Check this guide first
2. Review Render documentation
3. Check application logs
4. Test with minimal configuration
5. Contact Render support if needed

Remember: The free tier is perfect for development and testing, but consider upgrading for production workloads.