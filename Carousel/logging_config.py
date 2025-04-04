import logging
import logging.handlers
import queue 
import os
from django.conf import settings

class LoggingConfigurator:
    _listener = None

    @classmethod
    def initialize(cls):
        if cls._listener is not None:
            return

        # Ensure logs directory exists
        log_path = settings.LOGGING['handlers']['monthly_file']['filename']
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        if not os.path.exists(os.path.dirname(log_path)):
            raise Exception(f"Failed to create log directory: {os.path.dirname(log_path)}")

        # Create queue - THIS IS THE FIXED LINE
        log_queue = queue.Queue(-1)  # Now using queue.Queue instead of logging.handlers.Queue

        # Create handlers from Django config
        handlers = []
        for handler_config in settings.LOGGING['handlers'].values():
            handler = cls._create_handler(handler_config)
            if handler:
                handlers.append(handler)

        # Start listener
        cls._listener = logging.handlers.QueueListener(
            log_queue,
            *handlers,
            respect_handler_level=True
        )
        cls._listener.start()

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(settings.LOGGING['root']['level'])
        root_logger.addHandler(logging.handlers.QueueHandler(log_queue))

    @staticmethod
    def _create_handler(handler_config):
        handler_class = handler_config.get('class', '')
        
        if 'StreamHandler' in handler_class:
            handler = logging.StreamHandler()
        elif 'TimedRotatingFileHandler' in handler_class:
            handler = logging.handlers.TimedRotatingFileHandler(
                filename=handler_config['filename'],
                when=handler_config.get('when', 'midnight'),
                interval=handler_config.get('interval', 30),
                backupCount=handler_config.get('backupCount', 12),
                encoding=handler_config.get('encoding', 'utf8')
            )
        else:
            return None

        formatter_name = handler_config.get('formatter', '')
        if formatter_name in settings.LOGGING['formatters']:
            handler.setFormatter(logging.Formatter(
                settings.LOGGING['formatters'][formatter_name]['format']
            ))
        return handler

    @classmethod
    def shutdown(cls):
        if cls._listener:
            cls._listener.stop()