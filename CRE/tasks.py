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
from .models import StaffShift
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
        
        print("[SUCCESS] Email sent!")
        return True
        
    except Exception as e:
        logger.error(f"Email retry failed for user {user_id}: {e}")