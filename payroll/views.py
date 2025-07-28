import json
import asyncio

from adrf.views import APIView
from adrf.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework import status
from asgiref.sync import sync_to_async
from aiokafka import AIOKafkaProducer
from django.conf import settings
from django.utils.translation import gettext as _
from .models import Rule, RuleTarget, Override, Record, Period
from .serializers import RuleSerializer, OverrideSerializer, RecordSerializer, PeriodSerializer
from .permissions import RulePermission
from zMisc.policies import ScopeAccessPolicy
from zMisc.atransactions import aatomic

import logging

logger = logging.getLogger(__name__)

# Kafka configuration
KAFKA_RULES_TOPIC = 'payroll.rules.updated'
KAFKA_OVERRIDES_TOPIC = 'payroll.overrides.updated'
KAFKA_PAYROLL_TOPIC = 'payroll.generate'

class RuleViewSet(ModelViewSet):
    """
    Handles POST /rules to create or update payroll rules asynchronously.
    """
    queryset = Rule.objects.filter(is_active=True)
    serializer_class = RuleSerializer
    permission_classes = (ScopeAccessPolicy, RulePermission, )
    
    async def create(self, request):
        data = request.data.copy()
        data['company'] = request.data.get('companies', [None])[0]
        data['restaurant'] = request.data.get('restaurants', [None])[0]
        data['branch'] = request.data.get('branches', [None])[0]
        serializer = self.serializer_class(data=data, context={'request': request})
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        validated_data = serializer.validated_data
        targets_data = validated_data.pop('targets', [])
        
        # @aatomic()
        async def create_rule():
            # try:
            # rule = await serializer.save()
            # Create main rule
            rule = await Rule.objects.acreate(
                created_by=request.user,
                **validated_data
            )

            # Bulk create targets if they exist
            # if targets_data:
            #     targets = [
            #         RuleTarget(rule=rule, **target_data)
            #         for target_data in targets_data
            #     ]
            #     await RuleTarget.objects.abulk_create(targets)
            # Process targets and categorize them for the Kafka event
            target_roles = []
            target_users = []
            
            if targets_data:
                targets_to_create = []
                for target_data in targets_data:
                    # Prepare for bulk create
                    targets_to_create.append(RuleTarget(rule=rule, **target_data))
                    
                    # Categorize for Kafka event
                    if target_data.get('target_type') == 'role':
                        target_roles.append(target_data['target_value'])
                    elif target_data.get('target_type') == 'user':
                        target_users.append(target_data['target_value'])
                
                await RuleTarget.objects.abulk_create(targets_to_create)

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
                    # 'target_roles': [
                    #     target.target_value async for target in rule.targets.filter(target_type='role')
                    # ],
                    # 'target_users': [
                    #     target.target_value async for target in rule.targets.filter(target_type='user')
                    # ],
                    'target_roles': target_roles,
                    'target_users': target_users,
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
            serialized_data = await sync_to_async(lambda: serializer.data)()
            return Response(serialized_data, status=status.HTTP_201_CREATED)
            # except Exception as e:
            #     logger.error(f"Rule creation failed: {str(e)}")
            #     return Response({"detail": "Failed to create rule"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response = await create_rule()
        return response

        # return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class OverrideCreateView(APIView):
    """
    Handles POST /overrides to create special-case overrides asynchronously.
    """
    permission_classes = (ScopeAccessPolicy, RulePermission, )
    async def post(self, request):
        data = request.data.copy()
        data['branch'] = request.data.get('branches', [None])[0]
        serializer = OverrideSerializer(data=data, context={'request': request})
        if await sync_to_async(serializer.is_valid)():
            override = await serializer.save()
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