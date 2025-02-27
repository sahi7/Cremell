from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
import json

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