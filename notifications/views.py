import dateutil.parser
from asgiref.sync import sync_to_async 
from asgiref.sync import async_to_sync
from rest_framework import status
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from adrf.viewsets import ModelViewSet, APIView
from adrf.viewsets import ViewSet
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from .models import Task, EmployeeTransfer, TransferHistory, RoleAssignment
from .tasks import process_transfer, send_role_assignment_email
from .serializers import TransferSerializer, TransferHistorySerializer, RoleAssignmentSerializer
from CRE.tasks import log_activity
from CRE.models import Shift, Branch, StaffAvailability
from zMisc.policies import ScopeAccessPolicy
from zMisc.permissions import TransferPermission, UserCreationPermission, RoleAssignmentPermission
from zMisc.atransactions import aatomic

import logging
logger = logging.getLogger(__name__)

CustomUser = get_user_model()

class TransferViewSet(ModelViewSet):
    """
    ViewSet for managing EmployeeTransfer instances asynchronously.

    Example Request (POST /transfers/):
    {
        "user": 1,
        "from_branch": 5,
        "to_branch": 6,
        "transfer_type": "temporary",
        "end_date": "2025-04-01T00:00:00Z"
    }
    - Initiates a temporary transfer, validated against the initiator's scope.
    """
    queryset = EmployeeTransfer.objects.select_related(
                'from_branch__restaurant',  # from_branch and its restaurant
                'to_branch__restaurant',    # to_branch and its restaurant
                'from_restaurant',          # from_restaurant
                'to_restaurant'             # to_restaurant
            )
    serializer_class = TransferSerializer
    permission_classes = (ScopeAccessPolicy, TransferPermission, )

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(ScopeAccessPolicy().get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    @action(detail=True, methods=['patch'], url_path='review')
    async def review(self, request, pk=None):
        """
        Review a transfer (approve or reject).

        Example Request (PATCH /transfers/1/review/):
        {
            "approve": true
        }
        - Approves the transfer, processed by Celery.
        """
        obj = await sync_to_async(self.get_object)()
        
        # Manually check object-level permissions
        await sync_to_async(self.check_object_permissions)(request, obj)
        
        transfer = await self.queryset.aget(pk=pk)
        if transfer.status != 'pending':
            return Response({"detail": _("Transfer already processed.")}, status=status.HTTP_400_BAD_REQUEST)

        approve = request.data.get('approve', False)
        reject = request.data.get('reject', False)

        if approve == reject:  # Both True or both False
            return Response({"detail": _("Must specify either approve or reject, not both or neither.")}, status=status.HTTP_400_BAD_REQUEST)

        process_transfer.delay(
            transfer.id,
            approve=approve,
            reject=reject,
            reviewer_id=request.user.id
        )
        return Response({"detail": _("Transfer review queued.")}, status=status.HTTP_202_ACCEPTED)
    
    async def perform_create(self, serializer):
        # Async checks for real-world scenarios
        employee_id = self.request.data.get('user')
        from_branch = self.request.data.get('from_branch')
        

        # if employee_id and from_branch:
        #     # Check shift conflicts
        #     active_shifts = await Shift.objects.filter(
        #         employee_id=employee_id, branch_id=from_branch, end_time__gte=timezone.now()
        #     ).acount()
        #     self.request.shift_conflicts = active_shifts > 0

        #     # Check overlapping requests
        #     existing = await EmployeeTransfer.objects.filter(
        #         user_id=employee_id, status='pending'
        #     ).acount()
        #     if existing > 0:
        #         raise serializers.ValidationError(_("Employee has a pending transfer request."))
        # else:
        #     self.request.shift_conflicts = False
        

    async def _notify_hierarchy(self, transfer_request, restaurant_id):
        """Notify restaurant hierarchy via Channels."""
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"restaurant_{restaurant_id}_hierarchy",
            {
                "type": "transfer.request",
                "transfer_id": transfer_request.id,
                "message": "New transfer request requires approval."
            }
        )

    async def create(self, request, *args, **kwargs):
        end_date_str = request.data.get('end_date')

        if end_date_str:
            try:
                # Parse ISO 8601 string to datetime
                end_date = dateutil.parser.isoparse(end_date_str)
                # Ensure end_date is in the future
                if end_date <= timezone.now():
                    raise serializers.ValidationError(_("end_date must be a future date."))
            except ValueError as e:
                raise serializers.ValidationError(_("end_date must be a valid ISO 8601 date (e.g., '2025-04-01T00:00:00Z')."))
            
        serializer = self.get_serializer(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        transfer = await serializer.asave()

        # Notify hierarchy
        if transfer.from_branch:
            branch = await Branch.objects.select_related('restaurant').aget(pk=transfer.from_branch.id)
            await self._notify_hierarchy(transfer, branch.restaurant.pk)

        # Auto-approve for RestaurantOwner within their restaurants
        if request.user.role == 'restaurant_owner' and (
            not transfer.to_branch or transfer.to_branch.restaurant_id in await sync_to_async(lambda: request.user.restaurants.values_list('id', flat=True))()
        ):
            process_transfer.delay(transfer.id, approve=True, reviewer_id=request.user.id)
        
        return Response(self.get_serializer(transfer).data, status=status.HTTP_201_CREATED)
    

class TransferHistoryViewSet(ModelViewSet):
    """
    ViewSet for viewing transfer history within scope.

    Example Request (GET /transfer-history/?branch=5):
    - Returns history for Branch #5 if in scope.
    """
    queryset = TransferHistory.objects.all()
    serializer_class = TransferHistorySerializer
    permission_classes = [UserCreationPermission]  # Reuse scope logic

    def get_queryset(self):
        user = self.request.user
        rules = UserCreationPermission.SCOPE_RULES.get(user.role, {})
        scopes = rules.get('scopes', {})
        
        branch_filter = scopes.get('branches')
        restaurant_filter = scopes.get('restaurants')
        
        qs = TransferHistory.objects.all()
        if branch_filter:
            allowed_branches = branch_filter(user, self.request.query_params.get('branch', []))
            qs = qs.filter(branch__id__in=allowed_branches)
        if restaurant_filter:
            allowed_restaurants = restaurant_filter(user, self.request.query_params.get('restaurant', []))
            qs = qs.filter(restaurant__id__in=allowed_restaurants)
        return qs
    

class RoleAssignmentViewSet(ViewSet):
    permission_classes = ( RoleAssignmentPermission, )

    async def send_notification(self, user_id, message):
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"user_{user_id}",
            {
                'type': 'user.notification',
                'message': message
            }
    )

    @action(detail=False, methods=['post'], url_path='send')
    async def send_assignment(self, request):
        """
        Sending an Invitation
        POST /api/a/role-assignment/send/
        {
            "target_user": 2, / "target_email" : "anonymoususer@email"
            "role": "cashier",
            "type": "invitation" / "transfer",
            "companies": [1], # ScopeAccessPolicy requires
        }
        """
        data = request.data.copy()
        field_mapping = {
            'companies': 'company',
            'countries': 'country',
            'restaurants': 'restaurant',
            'branches': 'branch'
        }
        for plural, singular in field_mapping.items():
            if plural in data:
                # Take the first ID (assuming single object per field)
                data[singular] = data[plural][0] if isinstance(data[plural], list) and data[plural] else None
                del data[plural]

        serializer = RoleAssignmentSerializer(data=data, context={'request': request})
        if await sync_to_async(serializer.is_valid)():
            # Create assignment in serializer using validated_data as **kwargs
            assignment = await serializer.asave()

            # Send email
            subject = f"{'Invitation to' if assignment.type == 'invitation' else 'Ownership Transfer for'} {assignment.role}"
            message = f"""
            You have been {'invited to become' if assignment.type == 'invitation' else 'offered ownership of'} a {assignment.role}.
            Click here to accept: {settings.INVITATION_ACCEPT_REDIRECT_BASE_URL}{assignment.token}
            This offer expires on {assignment.expires_at}.
            """
            target_user = await CustomUser.objects.aget(pk=assignment.target_user_id)
            recipient_email = assignment.target_email or target_user.email
            send_role_assignment_email.delay(assignment.id, subject, message, recipient_email)

            # Send notification if target_user exists
            if assignment.target_user:
                target_user_id = target_user.id
                await self.send_notification(
                    target_user_id,
                    f"New {assignment.type} for {assignment.role} received."
                )

            # Log activity
            log_activity.delay(
                user_id=request.user.id,
                activity_type=f'{assignment.type}_sent',
                details={
                    'type': 'RoleAssignment',
                    'target_user_id': target_user.id if assignment.target_user else None,
                    'target_email': assignment.target_email,
                    'role': assignment.role,
                    'assignment_id': assignment.id
                },
                # obj_id=None,
                # obj_type=None
            )

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='handle/(?P<token>[^/.]+)')
    async def handle_assignment(self, request, token):
        """
        POST /api/a/role-assignment/handle/<token>/
        {"action": "accept"} / {"action": "reject"}
        """
        try:
            assignment = await RoleAssignment.objects.select_related('target_user', 'initiated_by', 'company', 'country', 'restaurant', 'branch').aget(token=token, status='pending')
            if assignment.expires_at < timezone.now():
                assignment.status = 'expired'
                await assignment.asave()
                return Response({"error": _("Assignment expired.")}, status=status.HTTP_400_BAD_REQUEST)

            action = request.data.get('action')
            if action not in ['accept', 'reject']:
                return Response({"error": _("Invalid action. Use 'accept' or 'reject'.")}, status=status.HTTP_400_BAD_REQUEST)

            # Handle invitation for non-existing user
            target_user = assignment.target_user
            if not target_user and assignment.target_email:
                target_user = await CustomUser.objects.filter(email=assignment.target_email).afirst()
                if not target_user:
                    return Response(
                        {"error": _("Please register with this email to proceed.")},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                assignment.target_user = target_user
                await assignment.asave()

                log_activity.delay(
                    user_id=target_user.id,
                    activity_type='invitation_linked',
                    details={
                        'assignment_id': assignment.id,
                        'role': assignment.role,
                        'email': assignment.target_email
                    },
                    # obj_id=assignment.id,
                    # obj_type='role_assignment'
                )

            # Process accept or reject
            initiator = assignment.initiated_by
            if action == 'accept':
                # Map scope fields to their ManyToMany relations
                scope_mappings = {
                    'company': ('companies', assignment.company),
                    'country': ('countries', assignment.country),
                    'restaurant': ('restaurants', assignment.restaurant),
                    'branch': ('branches', assignment.branch),
                }

                # Update scope in bulk
                for field, (relation_name, value) in scope_mappings.items():
                    if value:
                        relation = getattr(target_user, relation_name)
                        await relation.aadd(value)

                # Update role
                if target_user.role:
                    await target_user.remove_from_group(target_user.role)
                target_user.role = assignment.role
                await target_user.add_to_group(assignment.role)

                # Handle ownership transfer
                if assignment.type == 'transfer' and assignment.restaurant:
                    await initiator.restaurants.aremove(assignment.restaurant)
                    await initiator.remove_from_group(assignment.role)
                    if not await initiator.restaurants.aexists():
                        initiator.role = 'restaurant_manager'  # Downgrade
                        await initiator.asave()

                assignment.status = 'accepted'
                message = _("User is now a {role}.").format(role=assignment.role)
            else:  # reject
                assignment.status = 'rejected'
                message = _("You have rejected the {type} for {role}.").format(
                                type=assignment.type,
                                role=assignment.role
                            )

            await target_user.asave()
            await assignment.asave()

            # Send notification
            await self.send_notification(target_user.id, message)

            # Log activity
            log_activity.delay(
                user_id=target_user.id,
                activity_type=f'{assignment.type}_{assignment.status}',
                details={
                    'role': assignment.role,
                    'assignment_id': assignment.id,
                    'action': action
                },
                # obj_id=assignment.id,
                # obj_type='role_assignment'
            )

            return Response(
                {"message": message},
                status=status.HTTP_200_OK
            )
        except RoleAssignment.DoesNotExist:
            return Response({"error": _("Invalid assignment.")}, status=status.HTTP_404_NOT_FOUND)
        
from zMisc.utils import validate_order_role
from .tasks import update_staff_availability, update_order_status
class TaskClaimView(APIView):
    async def post(self, request):
        task_id = request.data['task_id']
        user = request.user
        try:
            @aatomic()
            async def claim_task():
                task = await Task.objects.select_for_update().filter(
                    id=task_id, status='pending', version=request.data['version']
                ).afirst()
                if not task:
                    return Response({'error': _('Task unavailable or modified')}, status=400)
                if not await validate_order_role(user, task.task_type):
                    return Response({'error': _("Invalid role, can't claim task")}, status=403)
                availability = await StaffAvailability.objects.aget(user=user)
                if availability.status != 'available':
                    return Response({'error': _('Has a task in progress -- Staff not available')}, status=400)
                task.status = 'claimed'
                task.claimed_by = user
                task.claimed_at = timezone.now()
                task.version += 1
                await task.asave()
                await update_staff_availability.delay(task.id)
                await update_order_status.delay(task.order.id, 'preparing', task.order.version)
                # await publish_event('task.claimed', {
                #     'task_id': task.id,
                #     'order_id': task.order.id,
                #     'branch_id': task.order.branch.id,
                #     'user_id': user.id
                # })
                return Response({'task_id': task.id}, status=200)
            await claim_task()
        except Exception as e:
            logger.error(f"Task claim failed: {str(e)}")
            return Response({'error': 'Task claim failed'}, status=500)