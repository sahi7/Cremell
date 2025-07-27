import json
import asyncio

from adrf.views import APIView
from rest_framework.response import Response
from rest_framework import status
from asgiref.sync import sync_to_async
from aiokafka import AIOKafkaProducer
from django.conf import settings
from django.utils.translation import gettext as _
from .models import Rule, Override, Record, Period
from .serializers import RuleSerializer, OverrideSerializer, RecordSerializer, PeriodSerializer
from .permissions import RulePermission
from zMisc.policies import ScopeAccessPolicy

# Kafka configuration
KAFKA_RULES_TOPIC = 'payroll.rules.updated'
KAFKA_OVERRIDES_TOPIC = 'payroll.overrides.updated'
KAFKA_PAYROLL_TOPIC = 'payroll.generate'

class RuleCreateView(APIView):
    """
    Handles POST /rules to create or update payroll rules asynchronously.
    """
    permission_classes = (ScopeAccessPolicy, RulePermission, )
    async def post(self, request):
        serializer = RuleSerializer(data=request.data, context={'request': request})
        if await sync_to_async(serializer.is_valid)():
            rule = await serializer.asave()  # Asynchronous save using serializer
            # Publish Kafka event for rule update
            producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
            await producer.start()
            try:
                event = {
                    'rule_id': rule.id,
                    'scope': rule.scope,
                    'company_id': rule.company_id,
                    'restaurant_id': rule.restaurant_id,
                    'branch_id': rule.branch_id,
                    'target_roles': [
                        target.target_value for target in rule.targets.filter(target_type='role')
                    ],
                    'target_users': [
                        target.target_value for target in rule.targets.filter(target_type='user')
                    ],
                    'periods': [p.id async for p in Period.objects.filter(
                        year__gte=rule.effective_from.year,
                        month__gte=rule.effective_from.month
                    )]
                }
                await producer.send_and_wait(
                    KAFKA_RULES_TOPIC,
                    key=str(rule.id).encode('utf-8'),
                    value=json.dumps(event).encode('utf-8')
                )
            finally:
                await producer.stop()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class OverrideCreateView(APIView):
    """
    Handles POST /overrides to create special-case overrides asynchronously.
    """
    permission_classes = (ScopeAccessPolicy,)
    async def post(self, request):
        serializer = OverrideSerializer(data=request.data, context={'request': request})
        if await sync_to_async(serializer.is_valid)():
            override = await serializer.asave()
            # Publish Kafka event for override update
            producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
            await producer.start()
            try:
                event = {
                    'override_id': override.id,
                    'rule_id': override.rule_id,
                    'user_id': override.user_id,
                    'period_id': override.period_id,
                    'branch_id': override.branch_id,
                    'override_type': override.override_type,
                    'amount': float(override.amount) if override.amount is not None else None,
                    'percentage': float(override.percentage) if override.percentage is not None else None
                }
                await producer.send_and_wait(
                    KAFKA_OVERRIDES_TOPIC,
                    key=str(override.id).encode('utf-8'),
                    value=json.dumps(event).encode('utf-8')
                )
            finally:
                await producer.stop()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class GeneratePayrollView(APIView):
    """
    Handles POST /generate-payroll?period=July to trigger payroll generation asynchronously.
    Publishes an event to Kafka for asynchronous processing.
    """
    permission_classes = (ScopeAccessPolicy,)
    async def post(self, request):
        period_str = request.query_params.get('period')
        if not period_str:
            return Response({"error": _("Period is required")}, status=status.HTTP_400_BAD_REQUEST)

        try:
            month, year = map(int, period_str.split('/'))
            period = await Period.objects.aget(month=month, year=year)
        except (ValueError, Period.DoesNotExist):
            return Response({"error": _("Invalid or non-existent period")}, status=status.HTTP_400_BAD_REQUEST)

        producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
        await producer.start()
        try:
            event = {
                'period_id': period.id,
                'month': period.month,
                'year': period.year
            }
            await producer.send_and_wait(
                KAFKA_PAYROLL_TOPIC,
                key=str(period.id).encode('utf-8'),
                value=json.dumps(event).encode('utf-8')
            )
        finally:
            await producer.stop()

        return Response({"message": _("Payroll generation triggered for {period}").format(period=period_str)})

class PayslipView(APIView):
    """
    Handles GET /payslip?period=July to retrieve a user's payslip asynchronously.
    """
    async def get(self, request):
        period_str = request.query_params.get('period')
        user = request.user

        if not period_str:
            return Response({"error": _("Period is required")}, status=status.HTTP_400_BAD_REQUEST)

        try:
            month, year = map(int, period_str.split('/'))
            period = await Period.objects.aget(month=month, year=year)
        except (ValueError, Period.DoesNotExist):
            return Response({"error": _("Invalid or non-existent period")}, status=status.HTTP_400_BAD_REQUEST)

        try:
            record = await Record.objects.aget(user=user, period=period)
            serializer = RecordSerializer(record)
            # response_data = await sync_to_async(lambda: serializer.data)()
            return Response(serializer.data)
        except Record.DoesNotExist:
            return Response({"error": _("Payslip not found for {period}").format(period=period_str)},
                            status=status.HTTP_404_NOT_FOUND)