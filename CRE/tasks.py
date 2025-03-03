from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import StaffShift

@shared_task
def check_overdue_shifts():
    now = timezone.now()
    overdue_shifts = StaffShift.objects.filter(
        end_datetime__lte=now - timezone.timedelta(minutes=10),
        end_datetime__gte=now - timezone.timedelta(minutes=15),
    )
    channel_layer = get_channel_layer()
    for shift in overdue_shifts:
        availability = shift.user.availability
        if availability.status == 'busy':
            # Notify branch managers
            managers = CustomUser.objects.filter(
                role='branch_manager',
                branches=shift.shift.branch
            )
            for manager in managers:
                async_to_sync(channel_layer.group_send)(
                    f"user_{manager.id}",
                    {
                        'type': 'overtime_notification',
                        'message': f"{shift.user.username} is still busy 10 minutes after shift end on {shift.date}. Approve overtime?"
                    }
                )