from django.apps import AppConfig


class KafkaConsumerConfig(AppConfig):
    name = 'kafka_consumer'

    def ready(self):
        pass
