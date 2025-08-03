import os
from celery import Celery
from celery.schedules import crontab


# Set the default Django settings module for the 'celery' program
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Carousel.settings')  

# Create Celery instance and configure it using settings
app = Celery('Carousel')

# Load task modules from all registered Django app configs
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'expire-stale-shift-swaps': {
        'task': 'CRE.tasks.expire_stale_shift_swap_requests',
        # 'schedule': crontab(minute='*/1'),  # Run every minute
        'schedule': crontab(hour='*/24', minute=0),  # Run every 24 hours 
    },
}

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')