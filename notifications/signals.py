from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.conf import settings  
from redis.asyncio import Redis
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from CRE.models import CustomUser, StaffAvailability
from .models import Task, BranchActivity, EmployeeTransfer


# @receiver(post_save, sender=Task)
# def update_staff_availability(sender, instance, **kwargs):
#     from notifications.tasks import update_staff_availability
#     update_staff_availability.delay(instance.id, instance.claimed_by_id)
#     if instance.status == 'claimed':
#         StaffAvailability.objects.filter(user=instance.claimed_by).update(
#             status='busy',
#             current_task=instance
#         )
#         # Log activity
#         BranchActivity.objects.create(
#             branch=instance.order.branch,
#             activity_type='task_claim',
#             user=instance.claimed_by,
#             details={
#                 'task_id': instance.id,
#                 'order_id': instance.order.id,
#                 'task_type': instance.task_type
#             }
#         )
#     elif instance.status == 'completed':
#         StaffAvailability.objects.filter(user=instance.claimed_by).update(
#             status='available',
#             current_task=None
#         )
#         # Log activity
#         BranchActivity.objects.create(
#             branch=instance.order.branch,
#             activity_type='task_complete',
#             user=instance.claimed_by,
#             details={
#                 'task_id': instance.id,
#                 'order_id': instance.order.id,
#                 'duration': (timezone.now() - instance.created_at).total_seconds()
#             }
#         )

# @receiver(pre_save, sender=StaffAvailability) # TOD0: CHeck if there is a way to know when user is in overtime than running this signal everytime the user 'availability' is updated
# async def handle_shift_overtime(sender, instance, **kwargs):
#     shift = await instance.current_shift()
#     if shift and not shift.is_overtime_active:
#         if timezone.now() > shift.end_datetime:
#             shift.is_overtime = True
#             await shift.asave()

@receiver(post_save, sender=CustomUser)
async def invalidate_shift_cache(sender, instance, **kwargs):
    cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    cache_key = f"user_scopes:{instance.id}"
    await cache.delete(cache_key)

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