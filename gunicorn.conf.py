# Gunicorn configuration file for BuildSense Multi-Agent Engine
import multiprocessing

bind = "0.0.0.0:5000"
workers = multiprocessing.cpu_count() * 2 + 1
threads = 4
worker_class = "gthread"
timeout = 120 # AI Agent pipeline can take up to 30-60s
keepalive = 5
loglevel = "info"
accesslog = "-"
errorlog = "-"
capture_output = True
