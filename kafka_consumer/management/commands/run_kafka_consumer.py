import asyncio
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from django.core.management.base import BaseCommand
from django.conf import settings
from aiokafka import AIOKafkaConsumer
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging
import signal

# Configure logging
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs a Kafka consumer with threaded processing for WebSocket notifications.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.consumer = None
        self.thread_pool = None
        self.batch_queue = defaultdict(list)
        self.BATCH_INTERVAL = 1.0
        self.running = True

    def handle(self, *args, **options):
        logger.info("Starting Kafka consumer management command...")
        self.thread_pool = ThreadPoolExecutor(max_workers=20)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start_consumer())
        except Exception as e:
            logger.error(f"Consumer failed with error: {str(e)}")
        finally:
            self.running = False
            loop.run_until_complete(self.stop_consumer())
            loop.close()
            self.thread_pool.shutdown()

    async def start_consumer(self):
        self.consumer = AIOKafkaConsumer(
            'payroll.generated',
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id='websocket-notifier-group',
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='earliest',
            fetch_max_wait_ms=500,
            max_partition_fetch_bytes=1048576,
        )
        logger.info("Attempting to start Kafka consumer...")
        max_retries = 5
        for attempt in range(max_retries):
            try:
                await self.consumer.start()
                # logger.info(f"Consumer started, assigned partitions: {self.consumer.assignment()}")
                break
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed to start consumer: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        asyncio.create_task(self.send_batch())
        while self.running:
            message_batch = await self.consumer.getmany(timeout_ms=1000)
            if message_batch:
                logger.info(f"Fetched batch with {sum(len(msgs) for msgs in message_batch.values())} messages")
                tasks = []
                for tp, messages in message_batch.items():
                    for msg in messages:
                        logger.debug(f"Processing message from partition {tp.partition}")
                        loop = asyncio.get_event_loop()
                        task = loop.create_task(self.process_message(msg.value))  # Create coroutine task
                        tasks.append(task)
                await asyncio.gather(*tasks)  # Await all tasks
            await self.consumer.commit()

    async def send_batch(self):
        """Send batched WebSocket messages every second via channel layer."""
        channel_layer = get_channel_layer()
        last_batch_key = None
        while self.running:
            current_time = time.time()
            batch_key = current_time - (current_time % self.BATCH_INTERVAL)
            if batch_key != last_batch_key:  # Process only when batch key changes
                if self.batch_queue:
                    for key in list(self.batch_queue.keys()):  # Process all pending batches
                        messages = self.batch_queue[key][:10000]  # Limit to 10,000 messages
                        if messages:
                            user_messages = defaultdict(list)
                            for msg in messages:
                                print(f"msg: {msg}")  # Debug: Should print if messages exist
                                user_id = msg.get('user_id')
                                if user_id is not None:
                                    user_messages[user_id].append(msg.get('payload'))
                            for user_id, payloads in user_messages.items():
                                group_name = f"user_{user_id}"
                                for payload in payloads:
                                    try:
                                        await channel_layer.group_send(
                                            group_name,
                                            {
                                                "signal": "payroll",
                                                "type": "stakeholder.notification",
                                                "message": json.dumps(payload)
                                            }
                                        )
                                        print(f"Sent message to group {group_name} with payload {payload}")  # Debug: Confirm send
                                    except Exception as e:
                                        print(f"Failed to send to {group_name}: {str(e)}")  # Debug: Catch send failures
                            del self.batch_queue[key]  # Clear processed batch
                last_batch_key = batch_key
            await asyncio.sleep(self.BATCH_INTERVAL - (current_time % self.BATCH_INTERVAL))

    async def process_message(self, event):
        """Process a single Kafka message and queue for batching."""
        print(f"Processing event: {event}")  # Debug: Log the raw event
        period_id = event.get('period_id')
        user_ids = event.get('user_ids', [])
        sender = event.get('sender')
        notify_value = event.get('notify', 'false')
        notify = str(notify_value).lower() == 'true' if isinstance(notify_value, (str, bool)) else False
        current_time = time.time()
        batch_key = current_time - (current_time % self.BATCH_INTERVAL)

        if not all([period_id, sender]):  # Validate required fields
            print(f"Skipping invalid message: missing period_id or sender, event={event}")
            return

        sender_msg = {
            'type': 'payslip_generated',
            'period_id': period_id,
            'user_ids': user_ids,
            'notify_users': notify,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        self.batch_queue[batch_key].append({'user_id': sender, 'payload': sender_msg})
        print(f"Queued sender message for user_id={sender}, batch_key={batch_key}")  # Debug: Confirm queuing

        if notify:
            if user_ids and len(user_ids) > 0:
                user_msg = {
                    'type': 'payslip_ready',
                    'period_id': period_id,
                    'sender': sender,
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                }
                for user_id in user_ids:
                    self.batch_queue[batch_key].append({'user_id': user_id, 'payload': user_msg})
                    print(f"Queued user message for user_id={user_id}, batch_key={batch_key}")  # Debug: Confirm queuing

    async def stop_consumer(self):
        """Gracefully stop the consumer and cleanup."""
        if self.consumer:
            await self.consumer.stop()
        if self.thread_pool:
            self.thread_pool.shutdown(wait=True)
        logger.info("Kafka consumer and threads stopped.")

# Signal handling for graceful shutdown
def handle_shutdown(signum, frame):
    logger.info(f"Received signal {signum}, initiating shutdown...")
    Command().running = False

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)