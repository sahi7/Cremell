from .models import EmployeeTransfer, TransferHistory
from .tasks import process_transfer
from .serializers import TransferSerializer, TransferHistorySerializer
from asgiref.sync import sync_to_async 
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.viewsets import ModelViewSet
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
    permission_classes = [TransferPermission]

    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context=self.get_serializer_context())
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        transfer = await sync_to_async(serializer.save)(initiated_by=request.user)

        # Auto-approve for RestaurantOwner within their restaurants
        if request.user.role == 'restaurant_owner' and (not transfer.to_branch or transfer.to_branch.restaurant in request.user.restaurants.all()):
            process_transfer.delay(transfer.id, approve=True, reviewer_id=request.user.id)
        
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    
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
        transfer = self.get_object()
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
