from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from CRE.models import CustomUser, StaffAvailability
from .models import Task, BranchActivity, EmployeeTransfer
from .tasks import process_transfer

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

@receiver(post_save, sender=CustomUser)
def user_created_signal(sender, instance, created, **kwargs):
    """
    Trigger: post_save on CustomUser.
    Connects to: WebSocket (employee_updates_{role} group).
    Action: Sends user_created event.
    """
    if created:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'employee_updates_{instance.role}',
            {
                'type': 'user_created',
                'user_id': instance.id,
                'username': instance.username,
                'status': instance.status
            }
        )

@receiver(post_save, sender=EmployeeTransfer)
def transfer_created_signal(sender, instance, created, **kwargs):
    """
    Trigger: post_save on EmployeeTransfer.
    Connects to: Celery (process_transfer task).
    Action: Queues transfer processing.
    """
    if created:
        channel_layer = get_channel_layer()
        groups = [f'employee_updates_{instance.initiated_by.role}']
        if instance.to_branch and instance.to_branch.manager:
            groups.append(f'employee_updates_{instance.to_branch.manager.role}')
        if instance.to_restaurant and instance.to_restaurant.manager:
            groups.append(f'employee_updates_{instance.to_restaurant.manager.role}')
        for group in set(groups):
            async_to_sync(channel_layer.group_send)(
                group,
                {
                    'type': 'transfer_created',
                    'transfer_id': instance.id,
                    'user_id': instance.user.id,
                    'username': instance.user.username,
                    'message': str(_("Transfer requested for user: {username}").format(username=instance.user.username))
                }
            )