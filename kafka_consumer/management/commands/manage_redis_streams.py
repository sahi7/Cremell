import asyncio
import ujson
import logging
import time
import uuid
import traceback
from django.core.management.base import BaseCommand
from django.conf import settings
import redis.asyncio as redis  # Change to async Redis
from channels.layers import get_channel_layer

logger = logging.getLogger('streams')

class SubscriptionStreamProcessor:
    def __init__(self, timeout=10, max_retries=3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.ack_stream = 'rms.ack.stream'
        self.dlq_stream = 'rms.dlq.stream'
        self.group_name = 'backend_subscription_group'
        self.consumer_name = f'consumer_{uuid.uuid4()}'
        self.running = True
        
    async def initialize(self):
        """Initialize async Redis connection and consumer groups"""
        self.redis_conn = settings.ASYNC_REDIS
        
        # Create consumer groups
        try:
            await self.redis_conn.xgroup_create(self.ack_stream, self.group_name, id='0', mkstream=True)
            logger.info(f"Created consumer group {self.group_name} for {self.ack_stream}")
        except Exception as e:
            if 'BUSYGROUP' not in str(e):
                raise
        try:
            await self.redis_conn.xgroup_create(self.dlq_stream, self.group_name, id='0', mkstream=True)
            logger.info(f"Created consumer group {self.group_name} for {self.dlq_stream}")
        except Exception as e:
            if 'BUSYGROUP' not in str(e):
                raise

    async def send_subscription_stream(self, subscription_id, entity_type, entity_id, subscription_data):
        feature_ids = subscription_data.get('feature_ids', [])
        if not isinstance(feature_ids, list):
            feature_ids = []
        feature_ids_str = ujson.dumps(feature_ids)

        message = {
            'type': 'subscription.create',
            'subscription_id': str(subscription_id),
            'entity_type': entity_type,
            'entity_id': entity_id,
            'plan_id': str(subscription_data['plan_id']),
            'feature_ids': feature_ids_str,
        }
        logger.info(f"Sending to Redis stream rms.stream: {message}")
        try:
            stream_id = await self.redis_conn.xadd('rms.stream', message)
            # Store pending subscription
            pending_data = {
                'stream_id': stream_id,
                'message': ujson.dumps(message),
                'retry_count': 0,
                'created_at': time.time(),
            }
            await self.redis_conn.setex(
                f'pending_subscriptions:{subscription_id}',
                self.timeout,
                ujson.dumps(pending_data)
            )
            logger.info(f"Stored pending subscription {subscription_id} with stream ID {stream_id}")
            return stream_id
        except Exception as e:
            logger.error(f"Failed to send stream message: {e}")
            raise

    async def process_ack_dlq(self, message_queue, deserialize_semaphore):
        while self.running:
            try:
                # Read from ACK stream - using async Redis
                ack_entries = await self.redis_conn.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.ack_stream: '>'},
                    block=1000,
                    # count=10  # Limit number of messages per read 
                )
                
                if ack_entries:
                    for stream, messages in ack_entries:
                        for message_id, message in messages:
                            async with deserialize_semaphore:
                                subscription_id = message.get('subscription_id')
                                event_type = message.get('type')
                                if not subscription_id or not event_type:
                                    logger.warning(f"Invalid ACK message {message_id}: {message}")
                                    continue
                                logger.info(f"Received ACK for subscription {subscription_id}: {event_type}")
                                # Mark as successful
                                pending_key = f'pending_subscriptions:{subscription_id}'
                                if await self.redis_conn.exists(pending_key):
                                    await self.redis_conn.delete(pending_key)
                                    logger.info(f"Subscription {subscription_id} processed successfully")
                                    await message_queue.put({
                                        'type': 'subscription_success',
                                        'subscription_id': subscription_id,
                                        'message': f"Subscription {subscription_id} processed successfully"
                                    })
                                await self.redis_conn.xack(self.ack_stream, self.group_name, message_id)

                # Read from DLQ stream - using async Redis
                dlq_entries = await self.redis_conn.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.dlq_stream: '>'},
                    block=1000,
                    # count=10  # Limit number of messages per read 
                )
                
                if dlq_entries:
                    for stream, messages in dlq_entries:
                        for message_id, message in messages:
                            async with deserialize_semaphore:
                                subscription_id = message.get('subscription_id')
                                event_type = message.get('type')
                                error = message.get('error')
                                if not subscription_id or not event_type:
                                    logger.warning(f"Invalid DLQ message {message_id}: {message}")
                                    continue
                                if not event_type.startswith('subscription.'):
                                    continue  # Skip non-subscription DLQ messages
                                logger.warning(f"Received DLQ for subscription {subscription_id}: {error}")
                                pending_key = f'pending_subscriptions:{subscription_id}'
                                if await self.redis_conn.exists(pending_key):
                                    pending_data = ujson.loads(await self.redis_conn.get(pending_key))
                                    retry_count = pending_data.get('retry_count', 0)
                                    if retry_count < self.max_retries:
                                        # Retry with same subscription_id
                                        original_message = ujson.loads(pending_data['message'])
                                        stream_id = await self.redis_conn.xadd('rms.stream', original_message)
                                        pending_data['retry_count'] += 1
                                        pending_data['stream_id'] = stream_id
                                        pending_data['created_at'] = time.time()
                                        await self.redis_conn.setex(
                                            pending_key,
                                            self.timeout,
                                            ujson.dumps(pending_data)
                                        )
                                        logger.info(f"Retried subscription {subscription_id}, attempt {retry_count + 1}")
                                    else:
                                        logger.error(f"Subscription {subscription_id} failed after {self.max_retries} retries: {error}")
                                        await self.redis_conn.delete(pending_key)
                                        await message_queue.put({
                                            'type': 'subscription_failed',
                                            'subscription_id': subscription_id,
                                            'message': f"Subscription failed: {error}"
                                        })
                                await self.redis_conn.xack(self.dlq_stream, self.group_name, message_id)
                                
                # Small sleep to prevent tight looping when no messages
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing ACK/DLQ streams: {e}")
                await asyncio.sleep(1)

    async def check_timeouts(self, message_queue):
        logger.info("Starting check_timeouts task")
        while self.running:
            try:
                # Use async scan instead of keys for better performance
                keys = []
                async for key in self.redis_conn.scan_iter('pending_subscriptions:*'):
                    keys.append(key)
                    
                current_time = time.time()
                # if not keys:
                #     logger.info("No pending subscriptions found")
                for key in keys:
                    pending_data_str = await self.redis_conn.get(key)
                    if pending_data_str:
                        pending_data = ujson.loads(pending_data_str)
                        created_at = pending_data.get('created_at', 0)
                        logger.info(f"Processing key {key}: age={current_time - created_at:.2f}s, data={pending_data}")
                        # print(f"key found: {key}, age={current_time - created_at:.2f}s, data={pending_data}")
                        if current_time - created_at > self.timeout:
                            subscription_id = key.split(':')[-1]
                            retry_count = pending_data.get('retry_count', 0)
                            if retry_count < self.max_retries:
                                original_message = ujson.loads(pending_data['message'])
                                stream_id = await self.redis_conn.xadd('rms.stream', original_message)
                                pending_data['retry_count'] += 1
                                pending_data['stream_id'] = stream_id
                                pending_data['created_at'] = current_time
                                await self.redis_conn.setex(
                                    key,
                                    settings.REDIS_STREAM_ACK_TIMEOUT,
                                    ujson.dumps(pending_data)
                                )
                                logger.info(f"Retried timed-out subscription {subscription_id}, attempt {retry_count + 1}, new stream ID {stream_id}")
                                # print(f"Retried subscription {subscription_id}, attempt {retry_count + 1}")
                            else:
                                logger.error(f"Subscription {subscription_id} timed out after {self.max_retries} retries")
                                # print(f"Subscription {subscription_id} timed out after {self.max_retries} retries")
                                await self.redis_conn.delete(key)
                                await message_queue.put({
                                    'type': 'subscription_failed',
                                    'subscription_details': pending_data,
                                    'message': f"Subscription timed out after {self.max_retries} retries"
                                })
            except Exception as e:
                logger.error(f"Error checking timeouts: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(5)

    async def send_notifications(self, message_queue):
        logger.info("Starting send_notifications loop")
        channel_layer = get_channel_layer()
        while self.running:
            try:
                # Use wait_for with timeout to prevent hanging
                message = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                try:
                    await channel_layer.group_send(
                        'subscription_notifications',
                        {
                            'type': 'subscription_message',
                            'message': message
                        }
                    )
                    logger.info(f"Sent notification: {message}")
                except Exception as e:
                    logger.error(f"Error sending notification: {e}")
                message_queue.task_done()
            except asyncio.TimeoutError:
                # No message received, continue loop
                continue

    async def close(self):
        """Cleanup resources"""
        self.running = False
        if hasattr(self, 'redis_conn'):
            await self.redis_conn.close()

class Command(BaseCommand):
    help = 'Runs an async Redis Streams consumer for subscription ACK and DLQ processing.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.processor = None
        self.message_queue = asyncio.Queue()
        self.deserialize_semaphore = asyncio.Semaphore(100)

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=int,
            default=30,
            help='Timeout for pending subscriptions in seconds (default: 30)'
        )
        parser.add_argument(
            '--max-retries',
            type=int,
            default=3,
            help='Maximum number of retries for failed subscriptions (default: 3)'
        )

    async def start_processor(self, timeout, max_retries):
        self.processor = SubscriptionStreamProcessor(
            timeout=timeout,
            max_retries=max_retries
        )
        
        # Initialize async components
        await self.processor.initialize()
        
        logger.info("Redis Streams processor initialized successfully")

        # Spawn all tasks
        notification_task = asyncio.create_task(self.processor.send_notifications(self.message_queue))
        timeout_task = asyncio.create_task(self.processor.check_timeouts(self.message_queue))
        process_task = asyncio.create_task(
            self.processor.process_ack_dlq(self.message_queue, self.deserialize_semaphore)
        )
        
        # Keep tasks running until cancelled
        try:
            await asyncio.gather(process_task, timeout_task, notification_task, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled, shutting down")
        except Exception as e:
            logger.error(f"Error in task execution: {e}\n{traceback.format_exc()}")
            raise
        finally:
            await self.processor.close()

    def handle(self, *args, **options):
        timeout = options['timeout']
        max_retries = options['max_retries']

        logger.info(f"Starting Redis Streams processor with timeout={timeout}, max_retries={max_retries}")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start_processor(timeout, max_retries))
        except KeyboardInterrupt:
            logger.info("Shutting down Redis Streams processor")
            self.running = False
            loop.run_until_complete(self.processor.close())
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception as e:
            logger.error(f"Processor failed: {e}\n{traceback.format_exc()}")
            self.running = False
            raise
        finally:
            self.running = False
            loop.close()