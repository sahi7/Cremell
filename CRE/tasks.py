from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from allauth.account.utils import send_email_confirmation

from django.utils import timezone
from django.http import HttpRequest
from django.contrib.sites.models import Site
from django.contrib.auth import get_user_model
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.mail import send_mail

from notifications.models import RestaurantActivity, BranchActivity
from .models import StaffShift, Restaurant, Branch
from zMisc.utils import determine_activity_model
import logging

logger = logging.getLogger(__name__)
CustomUser = get_user_model()

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
    message = f"Hi {user.username},\n\nYou’ve been assigned as {field_name} to {object_type} ID {object_id}.\n\nRegards,\nTeam"
    send_mail(subject, message, 'from@localhost', [user.email], fail_silently=False)


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