import dateutil.parser
from asgiref.sync import sync_to_async 
from rest_framework import status
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from adrf.viewsets import ModelViewSet
from channels.layers import get_channel_layer
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import EmployeeTransfer, TransferHistory
from .tasks import process_transfer
from .serializers import TransferSerializer, TransferHistorySerializer
from CRE.models import Shift, Branch
from zMisc.policies import ScopeAccessPolicy
from zMisc.permissions import TransferPermission, UserCreationPermission

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
    queryset = EmployeeTransfer.objects.all()
    serializer_class = TransferSerializer
    permission_classes = (TransferPermission, ScopeAccessPolicy, )

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
        transfer = await EmployeeTransfer.objects.select_related(
                'from_branch__restaurant',  # from_branch and its restaurant
                'to_branch__restaurant',    # to_branch and its restaurant
                'from_restaurant',          # from_restaurant
                'to_restaurant'             # to_restaurant
            ).aget(pk=pk)
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
        end_date_str = self.request.data.get('end_date')

        if end_date_str:
            try:
                # Parse ISO 8601 string to datetime
                end_date = dateutil.parser.isoparse(end_date_str)
                # Ensure end_date is in the future
                if end_date <= timezone.now():
                    raise ValidationError(_("end_date must be a future date."))
            except ValueError as e:
                raise ValidationError(_("end_date must be a valid ISO 8601 date (e.g., '2025-04-01T00:00:00Z')."))

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
