# Vercel Deployment Guide

Complete guide for deploying R34Video TikTok Style App on Vercel.

## 🚀 Quick Deploy

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kaiser62/r34video)

## 📋 Prerequisites

1. **GitHub Account**: Connected to Vercel
2. **Vercel Account**: Free account at [vercel.com](https://vercel.com)
3. **Repository Access**: This repository cloned or forked

## 🔧 Deployment Steps

### 1. Connect to Vercel
1. Go to [Vercel Dashboard](https://vercel.com/dashboard)
2. Click "New Project"
3. Import your GitHub repository
4. Select the `r34video` repository

### 2. Configure Project
Vercel will automatically detect the configuration from `vercel.json`:

**Framework Preset**: Other
**Root Directory**: `./` (default)
**Build Command**: Auto-detected
**Output Directory**: Auto-detected

### 3. Environment Variables
The following are pre-configured in `vercel.json`:
```json
{
  "USE_PROXY": "false",
  "DEBUG_MODE": "false",
  "MAX_WORKERS": "1",
  "CACHE_SIZE": "32",
  "REQUEST_TIMEOUT": "10",
  "FLASK_ENV": "production"
}
```

### 4. Deploy
Click "Deploy" and Vercel will:
1. Build your application
2. Deploy to global edge network
3. Provide you with a live URL

## 🎯 Vercel-Specific Optimizations

### Serverless Functions
- **Function timeout**: 30 seconds (configured in vercel.json)
- **Memory limit**: 1024MB (Vercel default)
- **Cold start**: ~2-5 seconds
- **Concurrent executions**: Up to 1000

### Edge Network
- **Global CDN**: Content delivered from nearest edge
- **Auto-scaling**: Scales based on demand
- **Zero-config**: No server management needed

### Free Tier Limits
- **Function invocations**: 100GB-hrs/month
- **Bandwidth**: 100GB/month
- **Edge requests**: 100K/month
- **Build time**: 6000 minutes/month

## 📊 Performance Expectations

### Response Times
- **Cold start**: 2-5 seconds (first request)
- **Warm requests**: 200-500ms
- **Static assets**: ~50ms (CDN)
- **Video streaming**: Depends on external source

### Scaling
- **Auto-scaling**: Handles traffic spikes automatically
- **Global distribution**: Served from 70+ regions
- **Caching**: Intelligent edge caching

## 🛠️ Troubleshooting

### Common Issues

**Build Failures:**
```bash
# Check Python version compatibility
python --version
# Should be Python 3.9+
```

**Function Timeout:**
```bash
# Functions timeout after 30 seconds
# This is configured in vercel.json
```

**Memory Issues:**
```bash
# Reduce cache size if needed
CACHE_SIZE=16
```

**Import Errors:**
```bash
# Ensure all dependencies are in requirements.txt
pip freeze > requirements.txt
```

### Debug Mode
For development, you can enable debug mode:
1. Go to Project Settings → Environment Variables
2. Add `DEBUG_MODE=true`
3. Redeploy

## 🔍 Monitoring

### Built-in Analytics
- **Function invocations**: Request count and duration
- **Error tracking**: Automatic error logging
- **Performance metrics**: Response times and success rates

### Logs
Access logs via:
1. Vercel Dashboard → Project → Functions
2. Real-time logs during deployment
3. Error logs with stack traces

## 🔄 Updates & Maintenance

### Auto Deployment
- **Git integration**: Auto-deploys on push to main branch
- **Branch deployments**: Preview deployments for branches
- **Rollback**: One-click rollback to previous deployments

### Manual Deployment
```bash
# Using Vercel CLI
npm i -g vercel
vercel --prod
```

## 🚨 Production Checklist

Before deploying to production:

- [ ] Git configuration fixed (author email)
- [ ] Environment variables configured
- [ ] `vercel.json` configuration validated
- [ ] Dependencies listed in `requirements.txt`
- [ ] Function timeout appropriate (30s)
- [ ] Cache size optimized for serverless
- [ ] Debug mode disabled

## 📈 Scaling & Performance

### Automatic Optimizations
- **Edge caching**: Static assets cached globally
- **Function bundling**: Optimized Python imports
- **Auto-scaling**: Scales to zero when idle
- **Global deployment**: Multi-region availability

### Custom Optimizations
- **Cache tuning**: Adjust `CACHE_SIZE` based on usage
- **Timeout optimization**: Balance between functionality and cost
- **Memory management**: Serverless-optimized threading

## 🔗 Useful Links

- **Vercel Docs**: https://vercel.com/docs
- **Python Runtime**: https://vercel.com/docs/runtimes#python
- **Dashboard**: https://vercel.com/dashboard
- **CLI Documentation**: https://vercel.com/docs/cli

## 💡 Pro Tips

1. **Use Preview Deployments**: Test changes on branch deployments
2. **Monitor Function Duration**: Keep under 30s to avoid timeouts
3. **Leverage Edge Caching**: Cache static responses when possible
4. **Environment Variables**: Use Vercel dashboard for sensitive config
5. **Analytics**: Monitor usage to optimize performance

## 🆘 Support

If you encounter issues:
1. Check Vercel function logs
2. Verify `vercel.json` configuration
3. Test locally with Vercel CLI
4. Review Python runtime limitations
5. Contact Vercel support if needed

---

## 🎉 Benefits of Vercel Deployment

- ✅ **Zero Configuration**: Works out of the box
- ✅ **Global CDN**: Fast worldwide delivery
- ✅ **Auto Scaling**: Handles traffic automatically
- ✅ **Git Integration**: Deploy on every push
- ✅ **Preview Deployments**: Test before production
- ✅ **Analytics**: Built-in performance monitoring

Perfect for production workloads with global reach!