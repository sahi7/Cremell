from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Task

@receiver(post_save, sender=Task)
def broadcast_task_update(sender, instance, **kwargs):
    from .consumers import broadcast_to_channel
    broadcast_to_channel.delay(
        instance.order.branch_id,
        'task_update',
        {'task_id': instance.id, 'status': instance.status}
    )


@receiver(post_save, sender=Task)
def update_staff_availability(sender, instance, **kwargs):
    if instance.status == 'completed':
        StaffAvailability.objects.filter(
            user=instance.claimed_by
        ).update(
            status='available',
            current_task=None
        )
    elif instance.status == 'claimed':
        StaffAvailability.objects.filter(
            user=instance.claimed_by
        ).update(
            status='busy',
            current_task=instance
        )