import json
import logging

from redis.asyncio import Redis
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings

logger = logging.getLogger(__name__)

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


class BranchConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = None 
        self.last_ping = None
        self.current_status = ""
        user = self.scope['user']
        if not user.is_authenticated:
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
        self.group_name = f"{self.branch_id}_{user.role}"
        self.group_available = f"{self.branch_id}_{user.role}_available"
        self.group_busy = f"{self.branch_id}_{user.role}_busy"
        self.group_offline = f"{self.branch_id}_{user.role}_offline"
        self.group_break = f"{self.branch_id}_{user.role}_break"
        self.group_overtime = f"{self.branch_id}_{user.role}_overtime"
        
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.channel_layer.group_add(self.group_branch, self.channel_name)
        await self.channel_layer.group_add(self.group_available, self.channel_name)
        self.current_status = "available"

        await self.accept()
        
        # Update connection count
        # await self.update_connection_count(1)
    async def _switch_status_group(self, new_status):
        user_id = self.scope['user'].id
        role = self.scope['user'].role
        print(f"self.current_status: {self.current_status}")
        redis = Redis.from_url(settings.REDIS_URL)

        # Remove from old group
        await self.channel_layer.group_discard(f"{self.group_name}_{self.current_status}", self.channel_name)

        # Add to new group 
        await self.channel_layer.group_add(f"{self.group_name}_{new_status}", self.channel_name)

        # Update Redis 
        await redis.set(f"{self.branch_id}_{user_id}:status", new_status, ex=86400)
        await redis.srem(f"{self.branch_id}_{role}_{self.current_status}", user_id)
        await redis.sadd(f"{self.branch_id}_{role}_{new_status}", user_id)

        # pipe = redis.pipeline()
        # pipe.set(f"{self.branch_id}_{user_id}:status", new_status)  # Individual user status
        # pipe.srem(f"{self.branch_id}_{role}_{self.current_status}", user_id)  # Remove from old status set
        # pipe.sadd(f"{self.branch_id}_{role}_{new_status}", user_id)  # Add to new status set
        # await pipe.execute()  # Atomic operation

        self.current_status = new_status
        # self.last_ping = datetime.utcnow()
        self.last_ping = timezone.now()
        online_users = await redis.scard(f"{self.branch_id}") # Set cardinality

        # pipe.scard(f"{self.branch_id}")  # Total online users in branch
        # pipe.scard(f"{self.branch_id}_cook_available")  # Available cooks
        # results = await pipe.execute()

        online_users_count = {
            # "total": results[0],
            # "cook": results[1],
            "total": online_users,
            "cook": await redis.scard(f"{self.branch_id}_cook_available"),
            "runner": await redis.scard(f"{self.branch_id}_food_runner_available")
        }
        logger.info(f"online_users_count: {online_users_count}")

        # Send notifications 
        await self.channel_layer.group_send(f"{self.group_available}", {
            "type": "status.update",
            "user_id": user_id,
            "status": new_status,
            "role": role,
            # "last_seen": self.last_ping,
            "online": json.dumps(online_users_count)
        })

    async def receive(self, text_data):
        data = json.loads(text_data)
        print(f"Received: {data}")

        if data.get('type') == 'heartbeat':
            self.last_ping = datetime.utcnow()
        if data.get('type') == 'status_update':
            new_status = data.get('status') 
            await self._switch_status_group(new_status)

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name') and self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            await self.channel_layer.group_discard(self.group_branch, self.channel_name)
            await self.channel_layer.group_discard(f"{self.group_name}_{self.current_status}", self.channel_name)
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
