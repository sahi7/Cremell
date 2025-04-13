from .models import Task, EmployeeTransfer, TransferHistory
from CRE.models import CustomUser
from asgiref.sync import async_to_sync
from celery import shared_task
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist

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

@shared_task
def process_transfer(transfer_id, approve=False, reject=False, reviewer_id=None):
    """
    Process an EmployeeTransfer: approve or reject, update assignments, and log history.
    Args:
        transfer_id: ID of the transfer to process.
        approve: Boolean to approve the transfer.
        reject: Boolean to reject the transfer.
        reviewer_id: ID of the user approving/rejecting (optional).
    """
    try:
        transfer = EmployeeTransfer.objects.get(id=transfer_id)
        user = transfer.user
        initiator = transfer.initiated_by
        reviewer = CustomUser.objects.get(id=reviewer_id) if reviewer_id else None

        if transfer.status != 'pending':
            return  # Already processed

        # Approval/Rejection logic
        elif reject:
            transfer.status = 'rejected'
            transfer.save()
            status = 'rejected'
        elif approve:
            transfer.status = 'approved'
            transfer.save()
            status = 'approved'

            # Update user's assignments
            if transfer.to_branch:
                user.branches.add(transfer.to_branch)
                if transfer.transfer_type == 'permanent' and transfer.from_branch:
                    user.branches.remove(transfer.from_branch)
            if transfer.to_restaurant:
                user.restaurants.add(transfer.to_restaurant)
                if transfer.transfer_type == 'permanent' and transfer.from_restaurant:
                    user.restaurants.remove(transfer.from_restaurant)

            # Activate user if pending and branches assigned
            if user.status == 'pending' and user.branches.exists():
                user.status = 'active'
                user.save()

            # Schedule reversion for temporary transfers
            if transfer.transfer_type == 'temporary' and transfer.end_date:
                revert_transfer.apply_async((transfer_id,), eta=transfer.end_date)
        else:
            return  # Pending, awaiting approval

        # Log to TransferHistory
        from_entity = f"Branch #{transfer.from_branch.id}" if transfer.from_branch else (f"Restaurant #{transfer.from_restaurant.id}" if transfer.from_restaurant else "Unassigned")
        to_entity = f"Branch #{transfer.to_branch.id}" if transfer.to_branch else (f"Restaurant #{transfer.to_restaurant.id}" if transfer.to_restaurant else "Unassigned")
        TransferHistory.objects.create(
            user=user,
            branch=transfer.to_branch,
            restaurant=transfer.to_restaurant,
            transfer_type=transfer.transfer_type,
            from_entity=from_entity,
            to_entity=to_entity,
            initiated_by=initiator,
            status=status,
            end_date=transfer.end_date if status == 'approved' else None
        )

        # Notify via WebSocket
        channel_layer = get_channel_layer()
        groups = [f'employee_updates_{user.role}']  # Initiator
        if transfer.from_branch:
            from_manager = transfer.from_branch.manager
            if from_manager:
                groups.append(f'employee_updates_{from_manager.role}')
        if transfer.to_branch:
            to_manager = transfer.to_branch.manager
            if to_manager:
                groups.append(f'employee_updates_{to_manager.role}')
        if transfer.from_restaurant:
            from_manager = transfer.from_restaurant.manager
            if from_manager:
                groups.append(f'employee_updates_{from_manager.role}')
        if transfer.to_restaurant:
            to_manager = transfer.to_restaurant.manager
            if to_manager:
                groups.append(f'employee_updates_{to_manager.role}')
        if reviewer:
            groups.append(f'employee_updates_{reviewer.role}')

        for group in set(groups):
            async_to_sync(channel_layer.group_send)(
                group,
                {
                    'type': 'transfer_approved' if status == 'approved' else 'transfer_rejected',
                    'user_id': user.id,
                    'username': user.username,
                    'to_branch': transfer.to_branch.id if transfer.to_branch else None,
                    'to_restaurant': transfer.to_restaurant.id if transfer.to_restaurant else None,
                    'message': str(_("Transfer {status} for user: {username}").format(status=status, username=user.username))
                }
            )

    except ObjectDoesNotExist:
        pass  # Handle missing transfer/user gracefully


@shared_task
def revert_transfer(transfer_id):
    """
    Revert a temporary transfer by restoring original assignments and logging history.
    """
    try:
        transfer = EmployeeTransfer.objects.get(id=transfer_id)
        user = transfer.user

        if transfer.status != 'approved' or transfer.transfer_type != 'temporary' or not transfer.end_date or timezone.now() < transfer.end_date:
            return  # Not eligible for reversion

        # Revert assignments
        if transfer.to_branch and transfer.from_branch:
            user.branches.remove(transfer.to_branch)
            user.branches.add(transfer.from_branch)
        if transfer.to_restaurant and transfer.from_restaurant:
            user.restaurants.remove(transfer.to_restaurant)
            user.restaurants.add(transfer.from_restaurant)

        # Update status if no branches remain
        if not user.branches.exists() and user.status == 'active':
            user.status = 'pending'
            user.save()

        # Log reversion
        from_entity = f"Branch #{transfer.to_branch.id}" if transfer.to_branch else (f"Restaurant #{transfer.to_restaurant.id}" if transfer.to_restaurant else "Unassigned")
        to_entity = f"Branch #{transfer.from_branch.id}" if transfer.from_branch else (f"Restaurant #{transfer.from_restaurant.id}" if transfer.from_restaurant else "Unassigned")
        TransferHistory.objects.create(
            user=user,
            branch=transfer.from_branch,
            restaurant=transfer.from_restaurant,
            transfer_type=transfer.transfer_type,
            from_entity=from_entity,
            to_entity=to_entity,
            initiated_by=transfer.initiated_by,
            status='approved'
        )

        # Notify via WebSocket
        channel_layer = get_channel_layer()
        groups = [f'employee_updates_{user.role}']
        if transfer.from_branch and transfer.from_branch.manager:
            groups.append(f'employee_updates_{transfer.from_branch.manager.role}')
        if transfer.to_branch and transfer.to_branch.manager:
            groups.append(f'employee_updates_{transfer.to_branch.manager.role}')
        for group in set(groups):
            async_to_sync(channel_layer.group_send)(
                group,
                {
                    'type': 'transfer_reverted',
                    'user_id': user.id,
                    'username': user.username,
                    'to_branch': transfer.from_branch.id if transfer.from_branch else None,
                    'to_restaurant': transfer.from_restaurant.id if transfer.from_restaurant else None,
                    'message': str(_("Temporary transfer reverted for user: {username}").format(username=user.username))
                }
            )

    except ObjectDoesNotExist:
        pass