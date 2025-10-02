import asyncio
import json
from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync, sync_to_async
from allauth.account.utils import send_email_confirmation

from itertools import groupby
from django.core import mail
from django.utils import timezone
from django.http import HttpRequest
from django.contrib.sites.models import Site
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.mail import send_mail

from notifications.models import Task
from .models import StaffShift, Restaurant, Branch, Order, StaffAvailability, ShiftSwapRequest
from zMisc.utils import determine_activity_model, render_notification_template
import logging

logger = logging.getLogger('web')
CustomUser = get_user_model()

@shared_task
def create_staff_availability(user_id):
    try:
        user = CustomUser.objects.get(id=user_id)
        StaffAvailability.objects.create(
            user=user,
            status='offline'
        )
    except Exception as e:
        logger.error(f"Failed to create StaffAvailability for user {user_id}: {str(e)}")

@shared_task(bind=True, max_retries=3, acks_late=True)
def set_user_password(self, user_id, password):
    try:
        from cre.models import CustomUser 
        user = CustomUser.objects.get(id=user_id)
        user.set_password(password)
        user.save()
    except Exception as exc:
        logger.error(f"Password set failed for user {user_id}: {str(exc)}")
        retry_delay = 5 * (2 ** self.request.retries)
        # Trigger retry
        raise self.retry(
            exc=exc,
            countdown=retry_delay,  # Exponential backoff: 1min, 2min, 4min
            max_retries=3
        )
    return True

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

@shared_task(bind=True, max_retries=3)
def send_register_email(self, user_id):
    try:
        user = CustomUser.objects.get(id=user_id)

        request = HttpRequest()
        request.META['HTTP_HOST'] = Site.objects.get_current().domain
        request.site = Site.objects.get_current()
        # Add session middleware (required for messages)
        SessionMiddleware(lambda: None).process_request(request)
        request.session.save()  # Ensure session is initialized

        # Add message middleware
        MessageMiddleware(lambda: None).process_request(request)

        send_email_confirmation(request, user)
        
        # print("[SUCCESS] Email sent!")
        return True
        
    except Exception as e:
        logger.error(f"Email retry failed for user {user_id}: {e}")


@shared_task
def send_assignment_email(user_id, object_type, object_id, field_name):
    user = CustomUser.objects.get(id=user_id)
    subject = f"New Assignment: {object_type.capitalize()} {field_name}"
    message = f"Hi {user.username},\n\nYou’ve been {field_name} {object_type} ID {object_id}.\n\nRegards,\nTeam"
    send_mail(subject, message, 'wufxna@gmail.com', [user.email], fail_silently=False)
    return True

@shared_task
def send_shift_notifications(
    user_ids: list, 
    branch_id: int, 
    subject: str,
    message: str,
    template_name: str,
    extra_context: dict | None = None,
    send_to_manager: bool = False
):
    """Send shift schedule notifications to users in batches"""
    # Fetch user data (email, language, timezone)
    timezones = asyncio.run(CustomUser.get_timezone_language(user_ids))

    branch = Branch.objects.get(id=branch_id)
    restaurant_name = branch.restaurant.name
    company_name = branch.restaurant.company.name if hasattr(branch.restaurant, 'company') and branch.restaurant.company else 'N/A'
    additional_context = {
        'branch_name': branch.name,
        'restaurant_name': restaurant_name,
        'company_name': company_name
    }

    if send_to_manager:
        manager_id = branch.manager_id
        if manager_id:
            user_ids.append(branch.manager_id)

    if extra_context is None:
        extra_context = additional_context
    else:
        extra_context = {**extra_context, **additional_context}

    # Organize shift data by user
    stakeholders = []
    if 'employee_shift_data' in extra_context:
        for user_id in user_ids:
            if str(user_id) in extra_context['employee_shift_data']:
                stakeholders.append({
                    'id': user_id,
                    'email': (CustomUser.objects.get(id=user_id)).email,
                    'language': timezones[user_id]['language'],
                    'timezone': timezones[user_id]['timezone'],
                    'shifts': [{
                        'date': extra_context['employee_shift_data'][str(user_id)]['date_key'],
                        'shift_id': extra_context['employee_shift_data'][str(user_id)]['shift_id'],
                        'shift_name': extra_context['employee_shift_data'][str(user_id)]['shift_name']
                    }]
                })
    else:
        user_ids_set = set(user_ids) 
        users = CustomUser.objects.filter(id__in=user_ids_set).values('id', 'email', 'username')
        user_map = {user['id']: user for user in users}
        for user_id in user_ids:
            stakeholders.append({
                'id': user_id,
                'email': user_map[user_id]['email'],
                'username': user_map[user_id]['username'],
                'language': timezones[user_id]['language'],
                'timezone': timezones[user_id]['timezone']
            })
    
    # Batch emails by language and timezone
    sorted_stakeholders = sorted(stakeholders, key=lambda x: (x['language'], x['timezone']))
    connection = mail.get_connection()
    try:
        connection.open()
        for (lang, tz), group in groupby(sorted_stakeholders, key=lambda x: (x['language'], x['timezone'])):
            email_messages = []
            
            for user_data in group:
                # Add shifts to extra_context for template
                extra_context['shifts'] = user_data.get("shifts")
                
                # Render HTML content
                html_content = asyncio.run(render_notification_template(
                    user_data,
                    message=message,
                    template_name=template_name,
                    extra_context=extra_context
                ))
                
                # Create plain text fallback
                from django.utils.html import strip_tags
                plain_content = strip_tags(html_content)
                
                # Create email
                email = mail.EmailMultiAlternatives(
                    subject=subject,
                    body=plain_content,
                    from_email='wufxna@gmail.com',
                    to=[user_data['email']],
                    connection=connection
                )
                email.attach_alternative(html_content, "text/html")
                email_messages.append(email)
            
            # Send batch
            connection.send_messages(email_messages)
        
        return True
    finally:
        connection.close()

@shared_task
def log_bulk_activities(activities):
    """Bulk version of log_activity"""
    # from django.contrib.contenttypes.models import ContentType
    from notifications.models import BranchActivity  # Import your ActivityLog model

    logs = []
    for activity in activities:
        logs.append(BranchActivity(
            user_id=activity['user_id'],
            activity_type=activity['activity_type'],
            details=activity['details'],
            branch_id=activity['obj_id'],
            # content_type=ContentType.objects.get_for_model(activity['obj_type'])
        ))

    BranchActivity.objects.bulk_create(logs)

    return True

def chunks(lst, n):
    """Yield successive n-sized chunks from lst"""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

@shared_task
def expire_stale_shift_swap_requests():
    from datetime import timedelta
    # Define timeout period (e.g., 24 hours)
    timeout_hours = 24
    expiration_time = timezone.now() - timedelta(hours=timeout_hours)

    # Find pending requests older than timeout
    stale_requests = ShiftSwapRequest.objects.filter(
        status='pending',
        created_at__lt=expiration_time
    )

    # Early exit if no requests to process
    if not stale_requests.exists():
        return

    # Prepare bulk activity data
    activities = []
    for request in stale_requests:
        activities.append({
            'user_id': request.initiator_id,
            'activity_type': 'shift_swap_expired',
            'details': {
                "reason": _(f"Shift swap request {request.id} expired after {timeout_hours} hours"),
                "initiator": request.initiator_id,
                "swap_id": request.id,
                "initiator_shift": request.initiator_shift_id,
                "desired_date": request.desired_date.isoformat()
            },
            'obj_id': request.branch_id,
            'obj_type': 'branch'
        })
    # Batch process logs in chunks of 100
    for chunk in chunks(activities, 100):
        log_bulk_activities.delay(chunk)

    # Bulk update status
    updated_count = stale_requests.update(status='expired')

    return f"Expired {updated_count} shift swap requests"

@shared_task
def log_activity(user_id, activity_type, details=None, obj_id=None, obj_type=None):
    """
    Logs an activity for a target user as a background task, choosing the model based on their role scope.
    
    Args:
        user_id: ID of the CustomUser performing the action (e.g., request.user.id).
        activity_type: Matches ACTIVITY_CHOICES (e.g., 'manager_assign', 'status_update').
        details: JSON-compatible dict with additional info (optional).
        obj_id: ID of the object (e.g., restaurant_id or branch_id) (optional).
    """
    # Fetch the user
    try:
        user = CustomUser.objects.get(id=user_id)
    except CustomUser.DoesNotExist:
        # Log error or skip silently, depending on your needs
        return

    # Determine model and scope field (assuming this is a synchronous helper)
    model, scope_field = determine_activity_model(user, obj_type)

    # Validate and set scope_value from obj
    if obj_id:
        expected_obj = Restaurant if scope_field == 'restaurant' else Branch
        try:
            obj_instance = expected_obj.objects.get(id=obj_id)
        except expected_obj.DoesNotExist:
            ValueError(f"ID mismatch: {obj_id} does not match {obj_instance.id}")
        scope_value = obj_instance
    else:
        scope_value = None

    # Prepare activity data
    activity_data = {
        scope_field: scope_value,
        'activity_type': activity_type,
        'user': user,
        'details': details or {},
    }

    # Create activity
    model.objects.create(**activity_data)

    return True

channel_layer = get_channel_layer()
@shared_task
def send_to_kds(order_id, details=None):
# async def send_to_kds(order_id):
    try:
        # Fetch order with related branch
        order = Order.objects.select_related('branch').get(pk=order_id)
        dets = {
            'order_items': details.get('item_names'),
            'order_id': order.id,
            'order_number': order.order_number,
            'total_price': str(order.total_price)
        }

        # Define statuses to check
        target_statuses = {'preparing', 'ready'}

        if order.status in target_statuses:
            # Find the cook who claimed the prepare task
            task = Task.objects.filter(
                order=order,
                task_type='prepare',
                status='claimed'
            ).select_related('claimed_by').first()

            if task and task.claimed_by:
                # Send to specific cook
                async_to_sync(channel_layer.group_send)(
                    f"user_{task.claimed_by.id}",
                    {
                        'signal': 'order',
                        'type': 'stakeholder.notification',
                        'message': json.dumps({
                            **dets,
                            'event': 'order_modified',
                        })
                    }
                )
                log_activity.delay(details['user_id'] , 'order_modify', dets, order.branch_id, 'branch')
                logger.info(f"Sent notification to user_{task.claimed_by.id} for order {order_id}")
            else:
                logger.warning(f"No claimed prepare task found for order {order_id}")
        else:
            # Fallback to kitchen group
            async_to_sync(channel_layer.group_send)(
                f"{order.branch.id}_cook",
                {
                    'signal': 'order',
                    'type': 'branch.update',
                    'message': json.dumps({
                        **dets,
                        'event': 'new_order',
                    })
                }
            )
            log_activity.delay(details['user_id'] , 'order_create', dets, order.branch_id, 'branch')
            logger.info(f"Sent notification to branch consumer {order.branch.id}_cook for order {order_id}")

    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found")
    except Exception as e:
        logger.error(f"Failed to send KDS notification for order {order_id}: {str(e)}")
    
@shared_task
def send_to_pos(order_id):
    try:
        order = Order.objects.get(pk=order_id)     
        message_data = {
            'event': 'order_updated',
            'order_status': order.status,
            'order_number': order.order_number
        }
        if order.status == 'completed':
            groups = [
                f"{order.branch.id}_food_runner",
                f"{order.branch.id}_cashier",
                f"{order.branch.id}_shift_leader"
            ]
            for group_name in groups:
                async_to_sync(channel_layer.group_send)(
                    group_name,
                    {
                        'signal': 'order',
                        'type': 'branch.update',
                        'message': json.dumps(message_data)
                    }
                )
        elif order.status == 'cancelled':
            groups = [
                f"{order.branch.id}_food_runner",
                f"{order.branch.id}_cook",
                f"{order.branch.id}_shift_leader"
            ]
            for group_name in groups:
                async_to_sync(channel_layer.group_send)(
                    group_name,
                    {
                        'signal': 'order',
                        'type': 'branch.update',
                        'message': json.dumps(message_data)
                    }
                )
        else:
            async_to_sync(channel_layer.group_send)(
                f"{order.branch_id}_food_runner",
                {
                    'signal': 'order',
                    'type': 'branch.update',
                    'message': json.dumps(message_data)
                }
            )
    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found for POS notification")
    except Exception as e:
        logger.error(f"POS notification failed for order {order_id}: {str(e)}")

from notifications.tasks import send_notification_task
@shared_task(bind=False) 
def notify_and_log(
    user_id=None,
    m2m_field_ids=None
):
    # Compute first_ids in task
    try:
        user = CustomUser.objects.get(id=user_id)
        first_ids = {
            field: ids[0] if ids else None
            for field, ids in m2m_field_ids.items()
        }
        branch_id = first_ids.get('branches')
        company_id = first_ids.get('companies')
        restaurant_id = first_ids.get('restaurants')
        country_id = first_ids.get('countries')
        
        send_notification_task.delay(
            user_id=user_id,
            branch_id=branch_id,
            company_id=company_id,
            restaurant_id=restaurant_id,
            country_id=country_id,
            subject=_("Please confirm your email"),
            message="",
            template_name='cre/emails/email_confirmation_message.html',
            reg_mail=True
        )
        details = {'username': user.username, 'role': user.get_role_display()}
        log_activity.delay(user_id, 'staff_hire', details)
        pass
    except Exception as e:
        logger.error(f"Email failed for user {user_id}: {str(e)}")

from services.printer import ReceiptPrinter
@shared_task
def print_receipt_task(order_id):
    printer = ReceiptPrinter(connection_type='usb', vendor_id=0x04b8, product_id=0x0202)
    try:
        asyncio.run(printer.print_receipt(order_id))
    finally:
        asyncio.run(printer.close())