# Gunicorn configuration for 200 concurrent users
# Run with: gunicorn -c gunicorn.conf.py app:app

import multiprocessing

# Worker type — gevent handles many concurrent connections efficiently
worker_class = "gevent"
worker_connections = 100   # each worker handles up to 100 concurrent connections

# Number of worker processes
# Formula: (2 × CPU cores) + 1 — adjust based on your server
workers = multiprocessing.cpu_count() * 2 + 1

# Timeouts
timeout = 120           # kill worker if request takes > 120s
graceful_timeout = 30   # give workers 30s to finish on shutdown
keepalive = 5           # keep connections alive for 5s

# Binding
bind = "0.0.0.0:5000"

# Logging
accesslog = "logs/access.log"
errorlog  = "logs/error.log"
loglevel  = "info"

# Process naming
proc_name = "fdp_survey"

# Restart workers after this many requests (prevents memory leaks)
max_requests = 1000
max_requests_jitter = 100

# Preload app for faster worker startup
preload_app = True
