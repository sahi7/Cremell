import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils.translation import gettext_lazy as _

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
            logger.info(f"WebSocket disconnected for user group {self.user_group}, code: {close_code}")

    async def stakeholder_notification(self, event):
        """
        Handle stakeholder notification, sending message to client.
        """
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'overtime.notification',
            'message': message
        }))
        logger.debug(f"Sent notification to {self.user_group}: {message}")


class BranchConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.branch_id = self.scope['url_route']['kwargs']['branch_id']
        self.channel_type = self.scope['url_route']['kwargs']['channel_type']
        self.group_name = f"branch_{self.branch_id}_{self.channel_type}"
        
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        
        # Update connection count
        await self.update_connection_count(1)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        await self.update_connection_count(-1)

    @database_sync_to_async
    def update_connection_count(self, delta):
        channel, _ = BroadcastChannel.objects.get_or_create(
            branch_id=self.branch_id,
            channel_type=self.channel_type
        )
        channel.active_connections += delta
        channel.save()

    async def task_update(self, event):
        await self.send(text_data=json.dumps(event['data']))


class OrderConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.order_group = f"order_{self.scope['order_id']}"
        await self.channel_layer.group_add(self.order_group, self.channel_name)
        await self.accept()

    async def order_update(self, event):
        await self.send(text_data=json.dumps(event['data']))


class KitchenConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.branch_group = f"kitchen_{self.scope['branch_id']}"
        await self.channel_layer.group_add(self.branch_group, self.channel_name)
        await self.accept()


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
