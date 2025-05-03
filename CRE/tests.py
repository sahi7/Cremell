from django.test import TestCase
from django.contrib.auth.models import User
from asgiref.sync import sync_to_async 
from .serializers import CompanySerializer

class CompanyTests(TestCase):
    async def test_company_creation(self):
        user = await User.objects.acreate(username='test')
        data = {
            'name': 'Test Company',
            'about': 'Test About',
            'contact_email': 'test@localhost',
            'contact_phone': '+1234567890',
        }
        serializer = CompanySerializer(data=data, context={'request': type('Request', (), {'user': user})})
        is_valid = await sync_to_async(serializer.is_valid)(raise_exception=True)
        company = await serializer.save()
        self.assertIsNotNone(company.id)