import json
import time
import logging

from redis.asyncio import Redis
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
from datetime import datetime

logger = logging.getLogger(__name__)
redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
pipe = redis.pipeline()

class StakeholderConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """
        Handle WebSocket connection, adding authenticated user to their group.
        """
        user = self.scope['user']
        if user.is_authenticated:
            self.user_group = f"user_{user.id}"
            await self.channel_layer.group_add(self.user_group, self.channel_name)
            await self.accept()
            logger.info(f"WebSocket connected for user {user.id}")
        else:
            logger.warning("WebSocket connection rejected: User not authenticated")
            await self.close(code=4001)  # Unauthorized

    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection, removing user from their group.
        """
        if hasattr(self, 'user_group'):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)
            logger.info(f"WebSocket disconnected for group {self.user_group}, code: {close_code}")

    async def stakeholder_notification(self, event):
        """
        Handle stakeholder notification, sending message to client.
        """
        message = event['message']
        signal = event.get('signal', 'stakeholder')
        await self.send(text_data=json.dumps({
            'type': f'{signal}.notification',
            'message': message
        }))
        logger.debug(f"Sent notification to {self.user_group}: {message}")


HEARTBEAT_TIMEOUT = 60
class BranchConsumer(AsyncWebsocketConsumer):
    async def _set_user_status(self, branch_id: int, user_id: int, status: str, current_status: str = None):
        ZSET_KEY = f"{branch_id}_heartbeats"
        timestamp = int(time.time())
        if not current_status:
            current_status = await redis.hget(f"{branch_id}_{user_id}:status", "status")
        await redis.hset(f"{branch_id}_{user_id}:status", mapping={
            "status": status,
            "last_ping": timestamp
        })
        print(f"current-status: {current_status}")
        await redis.expire(f"{branch_id}_{user_id}:status", 3600)
        if current_status:
            await pipe.srem(f"{self.branch_id}_{self.user.role}_{current_status}", user_id)
        if status == 'offline':
            await pipe.srem(self.group_branch, user_id)
            await pipe.srem(self.group_name, user_id)
        
        # ZRANGE {branch_id}_heartbeats 0 -1 WITHSCORES 
        await redis.zadd(ZSET_KEY, {str(user_id): timestamp})
        stale_users = await redis.zrangebyscore(ZSET_KEY, 0, timestamp - HEARTBEAT_TIMEOUT)
        logger.info(f"drebeat users: {stale_users}")
        await pipe.execute()

    async def connect(self):
        self.group_name = None 
        self.current_status = ""
        self.user = self.scope['user']
        if not self.user.is_authenticated:
            logger.warning("WebSocket connection rejected: User not authenticated")
            await self.close(code=4001)  # Unauthorized
            return 0
        self.branch_id = self.scope.get('url_route', {}).get('kwargs', {}).get('branch_id')
        if not self.branch_id:
            query_string = self.scope.get('query_string', b'').decode()
            query_params = dict(q.split('=') for q in query_string.split('&') if '=' in q)
            self.branch_id = query_params.get('branch_id')
        # Validate branch_id against user's branches
        user_branches = self.scope.get('branches', [])
        if not self.branch_id or str(self.branch_id) not in [str(b) for b in user_branches]:
            logger.warning(f"WebSocket connection rejected: Invalid or unauthorized branch_id {self.branch_id} for user {user.id}")
            await self.close(code=4003)  # Forbidden
            return
        self.group_branch = f"{self.branch_id}"
        self.group_name = f"{self.branch_id}_{self.user.role}"
        self.group_available = f"{self.branch_id}_{self.user.role}_available"
        # self.group_busy = f"{self.branch_id}_{self.user.role}_busy"
        # self.group_offline = f"{self.branch_id}_{self.user.role}_offline"
        # self.group_break = f"{self.branch_id}_{self.user.role}_break"
        # self.group_overtime = f"{self.branch_id}_{self.user.role}_overtime"
        
        self.current_status = "available"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.channel_layer.group_add(self.group_branch, self.channel_name)
        await self.channel_layer.group_add(self.group_available, self.channel_name)
        await self._set_user_status(self.branch_id, self.user.id, self.current_status)
        await pipe.sadd(self.group_available, self.user.id)
        await pipe.sadd(self.group_branch, self.user.id)
        await pipe.sadd(self.group_name, self.user.id)
        await pipe.execute()

        await self.accept()
        
    async def _switch_status_group(self, branch_id: int, user_id: int, role: str, new_status: str):
        # user_id = self.scope['user'].id
        # role = self.scope['user'].role
        old_status = await redis.hget(f"{branch_id}_{user_id}:status", "status")

        if not new_status == old_status:
            # Remove from old group
            # await self.channel_layer.group_discard(f"{self.group_name}_{self.current_status}", self.channel_name)
            await self.channel_layer.group_discard(f"{branch_id}_{role}_{old_status}", self.channel_name)

            # Add to new group 
            # await self.channel_layer.group_add(f"{self.group_name}_{new_status}", self.channel_name)
            await self.channel_layer.group_add(f"{branch_id}_{role}_{new_status}", self.channel_name)

            # Update Redis 
            # await redis.set(f"{branch_id}_{user_id}:status", new_status, ex=3600)
            await self._set_user_status(branch_id, user_id, new_status, old_status)
            # await redis.srem(f"{branch_id}_{role}_{old_status}", user_id)
            if new_status not in ["offline", "break"]:
                await redis.sadd(f"{branch_id}_{role}_{new_status}", user_id)
                await redis.sadd(f"{branch_id}", user_id)

        ###############   TAB    ##########
            status, last_ping = await redis.hmget(f"{branch_id}_{user_id}:status", "status", "last_ping")
            # last_ping = await redis.hget(f"{branch_id}_{user_id}:status", "last_ping")
            online_users = await redis.scard(f"{branch_id}") # Set cardinality
            cooks = await redis.scard(f"{branch_id}_cook_available")
            runners = await redis.scard(f"{branch_id}_food_runner_available")
            last_ping_readable = datetime.fromtimestamp(int(last_ping)).isoformat()

            online_users_count = {
                "total": online_users,
                "cook": cooks,
                "runner": runners
            }
            logger.info(f"online_users_count: {online_users_count}")

            # Send notifications 
            await self.channel_layer.group_send(f"{self.group_branch}", {
                "type": "status.update",
                "user_id": user_id,
                "status": status,
                "role": role,
                "last_seen": last_ping_readable,
                "online": json.dumps(online_users_count)
            })

    async def receive(self, text_data):
        data = json.loads(text_data)

        if data.get('type') == 'heartbeat':
            self.last_ping = datetime.utcnow()
        if data.get('type') == 'status_update':
            new_status = data.get('status') 
            await self._switch_status_group(self.branch_id, self.user.id, self.user.role, new_status)

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name') and self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            await self.channel_layer.group_discard(self.group_branch, self.channel_name)
            await self.channel_layer.group_discard(f"{self.group_name}_{self.current_status}", self.channel_name)
        await self._set_user_status(self.branch_id, self.user.id, 'offline')
        await redis.zrem(f"{self.branch_id}_heartbeats", self.user.id)
        
        # await self.update_connection_count(-1)

    async def branch_update(self, event):
        signal = event.get('signal', 'branch')
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': f'{signal}.notification',
            'message': message
        }))

    async def status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_status_update',
            'user_id': event['user_id'],
            'status': event['status'],
            'last_seen': event['last_seen'],
            'role': event['role'],
            'online': event['online']
        }))

class OrderConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.order_group = f"order_{self.scope['order_id']}"
        await self.channel_layer.group_add(self.order_group, self.channel_name)
        await self.accept()

    async def order_update(self, event):
        await self.send(text_data=json.dumps(event['data']))


class KitchenConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        user_role = user.role
        if not user.is_authenticated:
            logger.warning("WebSocket connection rejected: User not authenticated")
            await self.close(code=4001)  # Unauthorized
            return

        # Extract branch_id from URL route or query parameters
        branch_id = self.scope.get('url_route', {}).get('kwargs', {}).get('branch_id')
        if not branch_id:
            query_string = self.scope.get('query_string', b'').decode()
            query_params = dict(q.split('=') for q in query_string.split('&') if '=' in q)
            branch_id = query_params.get('branch_id')

        # Validate branch_id against user's branches
        user_branches = self.scope.get('branches', [])
        if not branch_id or str(branch_id) not in [str(b) for b in user_branches]:
            logger.warning(f"WebSocket connection rejected: Invalid or unauthorized branch_id {branch_id} for user {user.id}")
            await self.close(code=4003)  # Forbidden
            return

        self.branch_group = f"kitchen_{branch_id}_{user_role}"
        await self.channel_layer.group_add(self.branch_group, self.channel_name)
        await self.accept()
        logger.info(f"WebSocket connected for user {user.id} to branch {branch_id}")

    async def disconnect(self, close_code):
        if hasattr(self, 'branch_group'):
            await self.channel_layer.group_discard(self.branch_group, self.channel_name)
        logger.info(f"WebSocket disconnected for user {self.scope['user'].id} with code {close_code}")

    async def receive(self, text_data=None, bytes_data=None):
        # Handle incoming WebSocket messages if needed
        pass

    async def order_notification(self, event):
        # Handle messages sent to the branch group
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'order.notification',
            'message': message
        }))


class EmployeeUpdateConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time employee updates.
    """
    async def connect(self):
        user = self.scope['user']
        if user.is_anonymous:
            await self.close()
            return
        print(f"WebSocket connected: {self.channel_name}")
        print(self.scope['user'].role)

        self.group_name = f'employee_updates_{user.role}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def user_created(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_created',
            'user_id': event['user_id'],
            'status': event['status'],
            'message': str(_("User created: {username}").format(username=event['username']))
        }))
