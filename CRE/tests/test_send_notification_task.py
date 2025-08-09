import pytest
import asyncio
from unittest.mock import patch
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives
from django.contrib.sites.models import Site
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from cre.models import CustomUser
from notifications.tasks import send_notification_task
from celery.exceptions import Retry

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,  # Run Celery tasks synchronously
    ACCOUNT_EMAIL_CONFIRMATION_HMAC=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_ID=1
)
async def test_send_notification_task_generates_valid_activate_url(django_db_setup, django_db_blocker):
    # Setup: Create Site and CustomUser
    async with django_db_blocker.unblock():
        site = await Site.objects.aget_or_create(id=1, domain='vtuyyf.com', name='vtuyyf.com')
        user = await CustomUser.objects.aget_or_create(
            id=1,
            email='wufxna@gmail.com',
            username='testuser',
            role='company_admin',
            password='secure123'
        )

        # Mock get_role_value
        async def mock_get_role_value():
            return 1
        user.get_role_value = mock_get_role_value

    # Act: Call the task with mock email sending
    with patch.object(EmailMultiAlternatives, 'send') as mock_email_send:
        result = await send_notification_task.apply_async(kwargs={
            'user_id': user.id,
            'message': 'Please confirm your email to activate your account.',
            'subject': 'Activate Your Account',
            'branch_id': None,
            'company_id': 1,
            'restaurant_id': None,
            'country_id': None,
            'extra_context': {'company_name': 'Test Company'},
            'template_name': 'account/email/email_confirmation_message.html'
        }).get()

        # Assert: Task completed successfully
        assert result is True

        # Assert: Email was sent
        assert mock_email_send.called
        email_call = mock_email_send.call_args_list[0]
        email = email_call[0][0]  # EmailMultiAlternatives instance
        assert email.subject == 'Activate Your Account'
        assert email.to == ['wufxna@gmail.com']
        assert email.from_email == 'wufxna@gmail.com'
        assert 'text/html' in email.alternatives[0][1]
        html_content = email.alternatives[0][0]
        assert 'Activate Account' in html_content
        assert 'testuser' in html_content
        assert 'company_admin' in html_content
        assert 'Test Company' in html_content

        # Extract activate_url from email content
        import re
        url_pattern = r'href="([^"]+)"'
        urls = re.findall(url_pattern, html_content)
        assert len(urls) > 0, "No activate_url found in email"
        activate_url = urls[0]
        assert activate_url.startswith('http://vtuyyf.com/accounts/confirm-email/')

        # Extract key from URL
        key = activate_url.split('/')[-2]

        # Assert: EmailAddress was created
        email_address = await EmailAddress.objects.aget(user=user, email=user.email)
        assert email_address.verified is False
        assert email_address.primary is True

        # Act: Simulate email confirmation
        confirmation = EmailConfirmationHMAC.from_key(key)
        assert confirmation is not None
        assert confirmation.email_address == email_address

        # Simulate confirmation request
        factory = RequestFactory()
        request = factory.get(activate_url)
        request.user = user
        confirmed_email = await asyncio.coroutine(confirmation.confirm)(request)

        # Assert: Email is confirmed
        assert confirmed_email == email_address
        email_address = await EmailAddress.objects.aget(user=user, email=user.email)
        assert email_address.verified is True

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_notification_task_no_user(django_db_setup, django_db_blocker):
    # Act: Call task with invalid user_id
    async with django_db_blocker.unblock():
        with pytest.raises(Retry):
            await send_notification_task.apply_async(kwargs={
                'user_id': 999,
                'message': 'Confirm',
                'subject': 'Activate',
                'template_name': 'account/email/email_confirmation_message.html'
            }).get()

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_notification_task_email_failure(django_db_setup, django_db_blocker):
    # Setup: Create user
    async with django_db_blocker.unblock():
        user = await CustomUser.objects.acreate(
            id=2,
            email='test2@vtuyyf.com',
            username='testuser2',
            role='company_admin',
            password='secure123'
        )

    # Act: Mock email send to fail
    with patch.object(EmailMultiAlternatives, 'send', side_effect=Exception('SMTP error')):
        with pytest.raises(Retry):
            await send_notification_task.apply_async(kwargs={
                'user_id': user.id,
                'message': 'Confirm',
                'subject': 'Activate',
                'template_name': 'account/email/email_confirmation_message.html'
            }).get()