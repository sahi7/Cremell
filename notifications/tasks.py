import asyncio
from redis import Redis
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist
from allauth.account.models import EmailConfirmationHMAC, EmailAddress

from .models import Task, EmployeeTransfer, TransferHistory, ShiftAssignmentLog, BranchActivity
from CRE.models import Order, CustomUser
from zMisc.utils import get_user_data, render_notification_template, get_stakeholders

import logging
logger = logging.getLogger(__name__)

cache = Redis.from_url(settings.REDIS_URL)

@shared_task
def monitor_task_timeouts():
    from .signals import broadcast_task_update

    timeout = timezone.now() - timezone.timedelta(minutes=3)
    expired_tasks = Task.objects.filter(
        status='pending',
        timeout_at__lte=timezone.now()
    )
    
    for task in expired_tasks:
        task.status = 'escalated'
        task.save()
        broadcast_task_update.send(sender=Task, instance=task)


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

@shared_task
def send_role_assignment_email(assignment_id, subject, message, recipient_email):
    send_mail(
        subject,
        message,
        'wufxna@gmail.com',
        [recipient_email],
        fail_silently=False
    )

@shared_task
def send_notification_task(
        user_id,
        message,
        subject,
        branch_id=None,
        company_id=None,
        restaurant_id=None,
        country_id=None,
        extra_context = {},
        template_name=None,
        reg_mail=None
    ):
    """Send a notification to a single user."""
    # Run async get_user_data
    user_data = asyncio.run(get_user_data(user_id, branch_id, company_id, restaurant_id, country_id))
    print("user_data: ", user_data)
    if reg_mail:
        email_address, created = EmailAddress.objects.get_or_create(
            user_id=user_data['id'],
            email=user_data['email'],
            defaults={'verified': False, 'primary': True}
        )
        # Generate email confirmation key using EmailConfirmationHMAC
        email_confirmation = EmailConfirmationHMAC(email_address=email_address)
        key = email_confirmation.key  # Signed key via signing.dumps

        # Generate activate_url
        activate_url = f"{settings.EMAIL_CONFIRM_REDIRECT_BASE_URL}{key}"

        # Update extra_context with activate_url
        extra_context = {**(extra_context or {}), 'activate_url': activate_url}
        extra_context['expiration_days'] = settings.ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS
    
    # Run async render_notification_template
    html_content = asyncio.run(render_notification_template(user_data, message, template_name, extra_context))
    
    # Create plain text fallback
    from django.utils.html import strip_tags
    plain_content = strip_tags(html_content)
    
    # Send email with both versions
    from django.core.mail import EmailMultiAlternatives
    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_content,  # Plain text version (required)
        from_email='wufxna@gmail.com',
        to=[user_data['email']],
    )
    email.attach_alternative(html_content, "text/html")  # HTML version
    email.send(fail_silently=False)

    return True

@shared_task
def send_batch_notifications(
        company_id=None,
        restaurant_id=None,
        branch_id=None,
        country_id=None,
        message=None,
        subject=None,
        max_role_value=5,
        include_lower_roles=False,
        limit=1000,
        offset=0,
        extra_context = {},
        template_name=None,
    ):
    """Send notifications to stakeholders in a specific scope."""
    # Run async get_stakeholders
    stakeholders = asyncio.run(get_stakeholders(
        company_id=company_id,
        restaurant_id=restaurant_id,
        branch_id=branch_id,
        country_id=country_id,
        max_role_value=max_role_value,
        include_lower_roles=include_lower_roles,
        limit=limit, offset=offset
    ))
    for stakeholder in stakeholders:
        print(f"ID: {stakeholder['id']}, Email: {stakeholder['email']}, Role: {stakeholder['role']}")
    
    # Batch emails by language and timezone to optimize rendering
    from itertools import groupby
    from django.core import mail
    connection = mail.get_connection()
    sorted_stakeholders = sorted(stakeholders, key=lambda x: (x['language'], x['timezone']))
    try:
        connection.open()
        channel_layer = get_channel_layer()
        for (lang, tz), group in groupby(sorted_stakeholders, key=lambda x: (x['language'], x['timezone'])):
            email_messages = []
            
            for user_data in group:
                # Render HTML content (unchanged)
                html_content = asyncio.run(render_notification_template( 
                    user_data,
                    message,
                    template_name=template_name,
                    extra_context=extra_context
                ))
                
                # Create plain text fallback
                from django.utils.html import strip_tags
                plain_content = strip_tags(html_content)
                
                # Create email with both versions
                email = mail.EmailMultiAlternatives(
                    subject=subject,
                    body=plain_content,  # Plain text version
                    from_email='wufxna@gmail.com',
                    to=[user_data['email']],
                    connection=connection
                )
                email.attach_alternative(html_content, "text/html")
                email_messages.append(email)
                
                # Send notification 
                group_name = f"user_{user_data['id']}"
                async_to_sync(channel_layer.group_send)(
                group_name,
                    {
                        "model": "general",
                        "type": "stakeholder.notification",
                        "message": message
                    }
                )

            # Send batch
            connection.send_messages(email_messages)

        return True
    finally:
        connection.close()

        
    # for (lang, tz), group in groupby(sorted_stakeholders, key=lambda x: (x['language'], x['timezone'])):
    #     emails = []
    #     for user_data in group:
    #         email_body = asyncio.run(render_notification_template(
    #             user_data,
    #             message,
    #             template_name=template_name,
    #             extra_context=extra_context
    #         ))
    #         emails.append((
    #             subject,
    #             email_body,
    #             'wufxna@gmail.com',
    #             [user_data['email']],
    #         ))
    #     send_mass_mail(emails, fail_silently=False)
    # return True

from django.db import IntegrityError
from CRE.tasks import send_shift_notifications
@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={'max_retries': 3},
    acks_late=True
)
def log_shift_assignment(
    branch_id, 
    user_id, 
    shift_id, 
    date, 
    action,
    original_user_id,
    new_user_id,
    original_shift_name,
    new_shift_name,
    original_date,
    new_date,
    changes
):
    try:
        ShiftAssignmentLog.objects.create(
            branch_id=branch_id,
            user_id=user_id,
            shift_id=shift_id,
            date=date,
            action=action
        )
    except IntegrityError:
        pass  # Silently skip duplicate records

    # Determine notification recipients and types
    user_ids = []
    notification_type = 'update'
    same_user = original_user_id == new_user_id
    
    if same_user:
        user_ids.append(new_user_id)
        if changes['date'] and changes['shift']:
            notification_type = 'date_and_shift_change'
        elif changes['date']:
            notification_type = 'date_change'
        elif changes['shift']:
            notification_type = 'shift_change'
    else:
        user_ids.extend([original_user_id, new_user_id])
        notification_type = 'reassignment'  # Unified type for reassignment to different user

    subject="Shift Assignment Update"
    message=f"Your shift '{original_shift_name}' has been updated"
    template_name="emails/shift_reassignment_notification.html"
    extra_context={
        'notification_type': notification_type,
        'original_user_id': original_user_id,
        'new_user_id': new_user_id,
        'original_shift_name': original_shift_name,
        'new_shift_name': new_shift_name,
        'date': date,
        'original_date': original_date
    }
    send_shift_notifications.delay(
        user_ids=user_ids,
        branch_id=branch_id,
        subject=subject,
        message="",  # Not used since template is provided
        template_name=template_name,
        extra_context=extra_context
    )
    # print("extra_context: ", extra_context)

    return True

channel_layer = get_channel_layer()
@shared_task
def create_initial_task(order_id):
    order = Order.objects.select_related('created_by').get(id=order_id)
    task = Task.objects.create(
        order=order,
        task_type='prepare',
        status='pending',
        timeout_at=timezone.now() + timezone.timedelta(minutes=10)
    )
    # Prepare structured JSON message
    message_data = {
        'event': 'new_task',
        'task_id': task.id,
        'task_type': task.task_type,
        'task_version': task.version,
        'order_number': order.order_number,
    }
    user_id = order.created_by_id
    group_name = f"{order.branch_id}_cook"
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            'signal': 'task',
            'type': 'branch.update',
            'message': json.dumps(message_data)
        }
    )
    if order.created_by.role != 'food_runner' and user_id:
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                'signal': 'task',
                'type': 'stakeholder.notification',
                'message': json.dumps(message_data)
            }
        )
    # publish_event('order.created', {'order_id': order_id, 'task_id': task.id})
    # notify_staff(order.branch_id, 'kitchen', f"New task {task.id} for order {order_id}")

import json
from CRE.models import StaffAvailability
from CRE.tasks import log_activity
from notifications.consumers import BranchConsumer
@shared_task
def update_staff_availability(task_id, user_id):
    try:
        with transaction.atomic():
            task = Task.objects.select_related('order').get(id=task_id) 
            availability = StaffAvailability.objects.get(user_id=task.claimed_by_id)
            cache_key = f"staff_availability:{task.claimed_by.id}"
            
            # Invalidate cache
            cache.delete(cache_key)
            
            # Update availability
            task_stat = task.status
            # Redis.srem(f"{task.order.branch_id}_{task.claimed_by.role}_{task_stat}", user_id)
            if task_stat in ['completed', 'escalated']:
                availability.status = 'available'
                # Redis.srem(f"{task.order.branch_id}_{task.claimed_by.role}_available", user_id)
                availability.current_task = None
            else:
                availability.status = 'busy'
                # Redis.srem(f"{task.order.branch_id}_{task.claimed_by.role}_busy", user_id)
                availability.current_task = task
            availability.save()
            
            # Rebuild cache
            cache_data = {
                'status': availability.status,
                'current_task_id': availability.current_task_id,
                'last_update': availability.last_update.isoformat()
            }
            cache.set(cache_key, json.dumps(cache_data), ex=60)
            
            # Log activity
            details={'task_id': task.id, 'order_id': task.order.id, 'claimed_by':task.claimed_by_id}
            log_activity.delay(user_id , 'task_claim', details, task.order.branch_id, 'branch')
            
            # # Update Channels group membership
            # await update_group_membership(
            #     task.claimed_by, task.order.branch.id, task.claimed_by.role,
            #     old_status, 'busy'
            # )
    except Exception as e:
        logger.error(f"Staff availability update failed for task {task_id}: {str(e)}")

from django.db import transaction
from CRE.tasks import send_to_pos
@shared_task
def update_order_status(order_id, new_status, expected_version):
    try:
        with transaction.atomic():
            order = Order.objects.filter(
                id=order_id, version=expected_version
            ).select_for_update().first()
            if not order:
                raise ValueError("Concurrent update detected")
            order.status = new_status
            order.version += 1
            order.save()
            send_to_pos.delay(order.id)
            # await publish_event('order.status_updated', {
            #     'order_id': order_id,
            #     'status': new_status
            # })
    except Exception as e:
        logger.error(f"Order status update failed for order {order_id}: {str(e)}")

@shared_task
def create_task(order_id, task_type):
    """Create a serve or payment task for the given order, avoiding duplicates."""
    if task_type not in ['serve', 'payment']:
        logger.error(f"Invalid task_type {task_type} for order {order_id}")
        return

    # Redis lock to prevent duplicate tasks
    client = Redis.from_url(settings.REDIS_URL)
    lock_key = f"task:order:{order_id}:{task_type}"
    lock = client.lock(lock_key, timeout=30)  # 10s lock timeout

    try:
        if not lock.acquire(blocking=False):  # Non-blocking lock attempt
            logger.info(f"Task already exists for order {order_id}, type {task_type}")
            return

        # Fetch order
        order = Order.objects.get(id=order_id)
        
        # Set timeout based on task_type
        timeout_duration = timezone.timedelta(hours=1) if task_type == 'payment' else timezone.timedelta(minutes=5)
        
        # Create task
        task = Task.objects.create(
            order=order,
            task_type=task_type,
            status='pending',
            timeout_at=timezone.now() + timeout_duration,
            version=1
        )

        message_data = {
            'event': 'task_update',
            'task_id': task.id,
            'task_type': task.task_type,
            'task_version': task.version,
            'order_number': order.order_number,
        }

        # Determine WebSocket group based on task_type
        group_name = f"{order.branch_id}_{'cashier' if task_type == 'payment' else 'food_runner'}"
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'signal': 'task',
                'type': 'branch.update',
                'message': json.dumps(message_data)
            }
        )
        user_id = order.created_by_id
        if order.created_by.role != 'food_runner' and user_id:
            async_to_sync(channel_layer.group_send)(
                f"user_{user_id}",
                {
                    'signal': 'task',
                    'type': 'stakeholder.notification',
                    'message': json.dumps(message_data)
                }
            )

        # # Publish task creation event
        # async_to_sync(publish_event)(
        #     'task.created',
        #     {
        #         'task_id': task.id,
        #         'order_id': order_id,
        #         'branch_id': order.branch.id,
        #         'task_type': task_type
        #     }
        # )

    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found for task creation")
    except Exception as e:
        logger.error(f"Task creation failed for order {order_id}, type {task_type}: {str(e)}")
    finally:
        if lock.locked():
            lock.release()
        client.close()

@shared_task
def invalidate_cache_keys(cache_patterns, object_ids):
    """
    Bulk invalidate Redis cache keys using patterns and IDs
    Args:
        cache_patterns: List of cache key patterns (e.g. ['user_scopes:{id}', 'user_perms:{id}'])
        object_ids: List of IDs to invalidate
    Usage examples:
    # invalidate_cache_keys.delay(['user_scopes:{id}'], [96, 97, 98])
    # invalidate_cache_keys.delay(['user_{id}_profile', 'org_{id}_settings'], [101, 102])
    """
    if not cache_patterns or not object_ids:
        return
    
    # redis = Redis.from_url(settings.REDIS_URL)
    
    with cache.pipeline() as pipe:
        for obj_id in object_ids:
            for pattern in cache_patterns:
                key = pattern.format(id=obj_id)
                pipe.delete(key)
        try:
            pipe.execute()
        except cache.RedisError as e:
            logger.error(f"Cache invalidation failed: {str(e)}")