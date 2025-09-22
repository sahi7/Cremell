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
from zMisc.utils import clean_request_data

import logging

logger = logging.getLogger(__name__)

# Kafka configuration
KAFKA_RULES_TOPIC = 'payroll.rules.updated'
KAFKA_OVERRIDES_TOPIC = 'payroll.overrides.updated'
KAFKA_PAYROLL_TOPIC = 'payroll.generate'
KAFKA_EVENTS_TOPIC= 'rms.events'

class RuleViewSet(ModelViewSet):
    """
    Handles POST /rules to create or update payroll rules asynchronously.
    Request data
    {
        "name": "Indemnité de Logement",
        "rule_type": "bonus",
        "amount": "30.00", | "percentage": null,
        "scope": "branch",
        "branch": [],
        "targets": [
            {"target_type": "role", "target_value": "cashier"}
        ]
    }
    """
    queryset = Rule.objects.filter(is_active=True)
    serializer_class = RuleSerializer
    permission_classes = (ScopeAccessPolicy, RulePermission, )
    
    async def create(self, request):
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['company'] = request.data.get('companies', [None])[0]
        data['restaurant'] = request.data.get('restaurants', [None])[0]
        data['branch'] = request.data.get('branches', [None])[0]
        print("v data: ", data)
        serializer = self.serializer_class(data=data, context={'request': request})
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        validated_data = serializer.validated_data
        targets_data = validated_data.pop('targets', [])
        
        @aatomic()
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
                    'type': KAFKA_RULES_TOPIC,
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
                    KAFKA_EVENTS_TOPIC,
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
    Request data
    {
        "rule": 101,
        "period": 1,
        "user": 5001,
        "override_type": "replace", | "percentage": 5.0,
        "branch": [1],
        "expires_at": null,
        "notes": "Adjusted bonus for performance"
    }
    """
    permission_classes = (ScopeAccessPolicy, RulePermission, )
    async def post(self, request):
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch'] = request.data.get('branches', [None])[0]
        serializer = OverrideSerializer(data=data, context={'request': request})
        if await sync_to_async(serializer.is_valid)():
            override = await serializer.asave()
            # Publish Kafka event for override update
            producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
            await producer.start()
            try:
                event = {
                    'type': KAFKA_OVERRIDES_TOPIC,
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
                    KAFKA_EVENTS_TOPIC,
                    key=str(override.id).encode('utf-8'),
                    value=json.dumps(event).encode('utf-8')
                )
            finally:
                await producer.stop()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class GeneratePayrollView(APIView):
    """
    Handles POST /payroll/generate/?period=July to trigger payroll generation asynchronously.
    Publishes an event to Kafka for asynchronous processing.
    """
    permission_classes = (ScopeAccessPolicy,)
    async def post(self, request):
        period_str = request.query_params.get('period')
        auto_notify = request.query_params.get('auto_notify', 'false')
        if not period_str:
            return Response({"error": _("Period is required")}, status=status.HTTP_400_BAD_REQUEST)

        try:
            month, year = map(int, period_str.split('/'))
            period = await Period.objects.aget(month=month, year=year)

            event = {
                'type': KAFKA_PAYROLL_TOPIC,
                'notify': auto_notify,
                'sender': request.user.id,
                'period_id': period.id,
                'month': period.month,
                'year': period.year
            }

            requested = {
                'company_id': request.data.get("companies", []),
                # 'country_id': request.data.get("countries", []),
                'restaurant_id': request.data.get("restaurants", []),
                'branch_id': request.data.get("branches", []),
            }
            for field, requested_ids in requested.items():
                if not requested_ids:
                    continue
                event[field] = requested_ids[0]
        except (ValueError, Period.DoesNotExist):
            return Response({"error": _("Invalid or non-existent period")}, status=status.HTTP_400_BAD_REQUEST)
        print("event: ", event)
        # Fetch the user's scope company and country
        producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
        await producer.start()
        try:
            
            await producer.send_and_wait(
                KAFKA_EVENTS_TOPIC,
                key=str(period.id).encode('utf-8'),
                value=json.dumps(event).encode('utf-8')
            )
        except Exception as e:
            logger.error("Failed to send event to Kafka: %s", str(e))
            return Response({"error": _("Failed to send payroll event to Kafka")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        finally:
            await producer.stop()
            logger.info("Kafka producer stopped")

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