from .celery import app as celery_app
from .logging_config import LoggingConfigurator

__all__ = ('celery_app',)

LoggingConfigurator.initialize()