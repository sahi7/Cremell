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
        log_dir = os.path.join(settings.BASE_DIR, 'logs')
        os.makedirs(log_dir, exist_ok=True)

        if not os.path.exists(log_dir):
            raise Exception(f"Failed to create log directory: {log_dir}")

        # Create queue
        log_queue = queue.Queue(-1)

        # Create handlers from Django config
        handlers = []
        for handler_name, handler_config in settings.LOGGING['handlers'].items():
            handler = cls._create_handler(handler_config)
            if handler:
                handlers.append(handler)
                logging.info(f"Initialized handler: {handler_name}")

        # Start listener
        cls._listener = logging.handlers.QueueListener(
            log_queue,
            *handlers,
            respect_handler_level=True
        )
        cls._listener.start()
        logging.info("QueueListener started")

        # Configure root logger
        root_logger = logging.getLogger()
        root_level = settings.LOGGING.get('root', {}).get('level', 'INFO')
        root_logger.setLevel(root_level)
        root_logger.addHandler(logging.handlers.QueueHandler(log_queue))

    @staticmethod
    def _create_handler(handler_config):
        handler_class = handler_config.get('class', '')
        
        try:
            if 'StreamHandler' in handler_class:
                handler = logging.StreamHandler()
            elif 'TimedRotatingFileHandler' in handler_class:
                handler = logging.handlers.TimedRotatingFileHandler(
                    filename=handler_config['filename'],
                    when=handler_config.get('when', 'midnight'),
                    interval=handler_config.get('interval', 30),
                    backupCount=handler_config.get('backupCount', 12),
                    encoding=handler_config.get('encoding', 'utf8'),
                    delay=handler_config.get('delay', True)
                )
            else:
                logging.warning(f"Unsupported handler class: {handler_class}")
                return None

            formatter_name = handler_config.get('formatter', '')
            if formatter_name in settings.LOGGING['formatters']:
                handler.setFormatter(logging.Formatter(
                    settings.LOGGING['formatters'][formatter_name]['format']
                ))
            return handler
        except Exception as e:
            logging.error(f"Failed to create handler: {e}")
            return None

    @classmethod
    def shutdown(cls):
        if cls._listener:
            cls._listener.stop()
            logging.info("QueueListener stopped")