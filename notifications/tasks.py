from celery import shared_task
from .models import Task

@shared_task
def monitor_task_timeouts():
    from django.utils import timezone
    from .models import Task
    from .signals import broadcast_task_update

    timeout = timezone.now() - timedelta(minutes=3)
    expired_tasks = Task.objects.filter(
        status='pending',
        timeout_at__lte=timezone.now()
    )
    
    for task in expired_tasks:
        task.status = 'escalated'
        task.save()
        broadcast_task_update.send(sender=Task, instance=task)

@shared_task
def broadcast_to_channel(branch_id, message_type, data):
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    
    channel_layer = get_channel_layer()
    group_name = f"branch_{branch_id}_{data.get('channel_type', 'kitchen')}"
    
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "task.update",
            "data": {
                "type": message_type,
                **data
            }
        }
    )