import asyncio
import ujson
import time
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.conf import settings
from aiokafka import AIOKafkaConsumer
from channels.layers import get_channel_layer
import logging

# Configure logging
logger = logging.getLogger('streams')

class Command(BaseCommand):
    help = 'Runs an async Kafka consumer with Queue for WebSocket notifications.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.consumer = None
        self.message_queue = asyncio.Queue()  # Shared queue for messages
        self.batch_queue = defaultdict(list)
        self.BATCH_INTERVAL = 1.0
        self.running = True
        self.channel_layer = get_channel_layer()
        self.deserialize_semaphore = asyncio.Semaphore(100)  # Limit concurrent deserializations

    async def start_consumer(self):
        self.consumer = AIOKafkaConsumer(
            settings.CONSUMER_TOPIC,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            value_deserializer=lambda m: m,  # Raw bytes
            auto_offset_reset='earliest',
            fetch_max_wait_ms=500,
            max_partition_fetch_bytes=1048576,
        )
        logger.info("Attempting to start Kafka consumer...")
        max_retries = 5
        for attempt in range(max_retries):
            try:
                await self.consumer.start()
                logger.info(f"Consumer started, assigned partitions: {self.consumer.assignment()}")
                break
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed to start consumer: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

        # Spawn consumer, processor, and sender tasks
        consume_task = asyncio.create_task(self.consume_messages())
        process_task = asyncio.create_task(self.process_messages())
        send_task = asyncio.create_task(self.send_batch())  # Ensure send_batch is a task
        await asyncio.gather(consume_task, process_task, send_task)

    async def deserialize_message(self, m):
        """Custom deserializer using ujson with CPU management."""
        try:
            async with self.deserialize_semaphore:
                return ujson.loads(m.decode('utf-8'))
        except (ujson.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"JSON decode error: {str(e)}, raw message: {m}")
            return None

    async def consume_messages(self):
        while self.running:
            try:
                message_batch = await self.consumer.getmany(timeout_ms=1000)
                if message_batch:
                    logger.info(f"Fetched batch with {sum(len(msgs) for msgs in message_batch.values())} messages")
                    tasks = []
                    for tp, messages in message_batch.items():
                        for msg in messages:
                            if msg.value:  # Ensure message is valid
                                # Deserialize async with semaphore
                                async with self.deserialize_semaphore:
                                    deserialized = await self.deserialize_message(msg.value)
                                    if deserialized:
                                        tasks.append(self.message_queue.put(deserialized))
                    await asyncio.gather(*tasks)
                await self.consumer.commit()
            except Exception as e:
                logger.error(f"Error in consume_messages: {str(e)}")
                await asyncio.sleep(1)

    async def process_messages(self):
        while self.running:
            try:
                event = await self.message_queue.get()
                if event:
                    await self.handle_message(event)
                self.message_queue.task_done()
            except Exception as e:
                logger.error(f"Error in process_messages: {str(e)}")
                await asyncio.sleep(1)

    async def handle_message(self, event):
        """Dispatch message to appropriate handler based on type."""
        try:
            event['type'] = 'payroll.generated'
            event_type = event.get('type', 'unknown')
            handlers = {
                'payroll.generated': self.handle_payroll,
                'inventory': self.handle_inventory,
            }
            handler = handlers.get(event_type, self.handle_unknown)
            await handler(event)
        except Exception as e:
            logger.error(f"Error handling message {event}: {str(e)}")

    async def handle_payroll(self, event):
        """Handle payroll-generated messages with WebSocket notifications."""
        # print(f"Processing payroll event: {event}")
        period_id = event.get('period_id')
        user_ids = event.get('user_ids', [])
        sender = event.get('sender')
        notify_value = event.get('notify', 'false')
        notify = str(notify_value).lower() == 'true' if isinstance(notify_value, (str, bool)) else False

        if not all([period_id, sender]):
            print(f"Skipping invalid payroll message: missing period_id or sender, event={event}")
            return

        current_time = time.time()
        batch_key = current_time - (current_time % self.BATCH_INTERVAL)

        sender_msg = {
            'type': 'payslip.generated',
            'period_id': period_id,
            'user_ids': user_ids,
            'notify_users': notify,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        self.batch_queue[batch_key].append({'user_id': sender, 'payload': sender_msg})
        print(f"Queued sender message for user_id={sender}, batch_key={batch_key}")

        if notify and user_ids:
            user_msg = {
                'type': 'payslip.ready',
                'period_id': period_id,
                'sender': sender,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            }
            for user_id in user_ids:
                self.batch_queue[batch_key].append({'user_id': user_id, 'payload': user_msg})
                print(f"Queued user message for user_id={user_id}, batch_key={batch_key}")

    async def handle_inventory(self, event):
        """Handle inventory messages (placeholder for custom logic)."""
        print(f"Processing inventory event: {event}")
        await asyncio.sleep(0.1)  # Simulate async work
        logger.info(f"Handled inventory message: {event}")

    async def handle_unknown(self, event):
        """Handle unknown message types."""
        logger.warning(f"Unknown message type for event: {event}")

    async def send_batch(self):
        """Send batched WebSocket messages asynchronously."""
        last_batch_key = None
        while self.running:
            try:
                current_time = time.time()
                batch_key = current_time - (current_time % self.BATCH_INTERVAL)
                if batch_key != last_batch_key and self.batch_queue:
                    for key in list(self.batch_queue.keys()):
                        messages = self.batch_queue[key][:10000]
                        if messages:
                            user_messages = defaultdict(list)
                            for msg in messages:
                                # print(f"msg: {msg}")
                                user_id = msg.get('user_id')
                                if user_id is not None:
                                    user_messages[user_id].append(msg.get('payload'))
                            for user_id, payloads in user_messages.items():
                                group_name = f"user_{user_id}"
                                for payload in payloads:
                                    await self.channel_layer.group_send(
                                        group_name,
                                        {
                                            "signal": "payroll",
                                            "type": "stakeholder.notification",
                                            "message": ujson.dumps(payload)
                                        }
                                    )
                                    # print(f"Sent message to group {group_name} with payload {payload}")
                        del self.batch_queue[key]
                    last_batch_key = batch_key
                await asyncio.sleep(self.BATCH_INTERVAL - (current_time % self.BATCH_INTERVAL))
            except Exception as e:
                logger.error(f"Error in send_batch: {str(e)}")
                await asyncio.sleep(1)

    async def stop_consumer(self):
        """Gracefully stop the consumer."""
        self.running = False
        if self.consumer:
            await self.consumer.stop()
        logger.info("Kafka consumer stopped.")
    
    def handle(self, *args, **options):
        logger.info("Starting Kafka consumer management command...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start_consumer())
        except KeyboardInterrupt:  # Catch Ctrl+C on Windows
            logger.info("Received KeyboardInterrupt, initiating shutdown...")
            self.running = False
            loop.run_until_complete(self.stop_consumer())
        except Exception as e:
            logger.error(f"Consumer failed with error: {str(e)}")
        finally:
            self.running = False
            loop.run_until_complete(self.stop_consumer())
            loop.close()  
    #     except Exception as e:
    #         logger.error(f"Consumer failed with error: {str(e)}")
    #     finally:
    #         loop.run_until_complete(self.stop_consumer())
    #         loop.close()

# # Signal handling for graceful shutdown
# async def handle_shutdown(signum, frame):
#     logger.info(f"Received signal {signum}, initiating shutdown...")
#     Command().running = False

# loop = asyncio.get_event_loop()
# for sig in (signal.SIGINT, signal.SIGTERM):
#     loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handle_shutdown(s, None)))

# if __name__ == "__main__":
#     Command().handle()