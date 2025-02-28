from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Task, StaffAvailability, BranchActivity

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
    if instance.status == 'claimed':
        StaffAvailability.objects.filter(user=instance.claimed_by).update(
            status='busy',
            current_task=instance
        )
        # Log activity
        BranchActivity.objects.create(
            branch=instance.order.branch,
            activity_type='task_claim',
            user=instance.claimed_by,
            details={
                'task_id': instance.id,
                'order_id': instance.order.id,
                'task_type': instance.task_type
            }
        )
    elif instance.status == 'completed':
        StaffAvailability.objects.filter(user=instance.claimed_by).update(
            status='available',
            current_task=None
        )
        # Log activity
        BranchActivity.objects.create(
            branch=instance.order.branch,
            activity_type='task_complete',
            user=instance.claimed_by,
            details={
                'task_id': instance.id,
                'order_id': instance.order.id,
                'duration': (timezone.now() - instance.created_at).total_seconds()
            }
        )

@receiver(pre_save, sender=StaffAvailability)
def handle_shift_overtime(sender, instance, **kwargs):
    if instance.current_shift and not instance.current_shift.is_overtime:
        if timezone.now() > instance.current_shift.end_time:
            instance.current_shift.is_overtime = True
            instance.current_shift.save()