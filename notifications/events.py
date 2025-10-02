from aiokafka import AIOKafkaProducer
import json
import logging

logger = logging.getLogger('web')

async def publish_event(event_type, data):
    try:
        producer = AIOKafkaProducer(bootstrap_servers='kafka:9092')
        await producer.start()
        await producer.send_and_wait(event_type, json.dumps(data).encode('utf-8'))
        await producer.stop()
    except Exception as e:
        logger.error(f"Failed to publish event {event_type}: {str(e)}")