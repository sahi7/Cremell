from .models import EmployeeTransfer
from .serializers import TransferSerializer 
from zMisc.permissions import TransferPermission

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
        return Response(serializer.data, status=status.HTTP_201_CREATED)