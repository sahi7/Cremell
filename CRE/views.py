from datetime import date
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework_simplejwt.views import TokenBlacklistView

from dj_rest_auth.registration.views import SocialLoginView
from dj_rest_auth.registration.views import RegisterView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from asgiref.sync import sync_to_async 
from adrf.views import APIView
from adrf.viewsets import ModelViewSet

# from .serializers import UserSerializer, CustomRegisterSerializer, RegistrationSerializer, RestaurantSerializer, BranchSerializer, BranchMenuSerializer, MenuSerializer, MenuCategorySerializer
# from .serializers import MenuItemSerializer, CompanySerializer, StaffShiftSerializer, OvertimeRequestSerializer
from .serializers import *
from .models import *
# from .models import Company, Restaurant, Branch, Menu, MenuItem, MenuCategory, Order, OrderItem, Shift, StaffShift, StaffAvailability, OvertimeRequest
from archive.tasks import finalize_deletion, handle_deletion_tasks
from zMisc.policies import RestaurantAccessPolicy, BranchAccessPolicy, ScopeAccessPolicy
# from zMisc.permissions import UserCreationPermission, RestaurantPermission, BranchPermission, ObjectStatusPermission, DeletionPermission
from zMisc.permissions import *
from zMisc.utils import validate_scope, validate_role
from zMisc.shiftresolver import ShiftUpdateHandler
import logging

logger = logging.getLogger(__name__)

CustomUser = get_user_model()
def email_confirm_redirect(request, key):
    return HttpResponseRedirect(
        f"{settings.EMAIL_CONFIRM_REDIRECT_BASE_URL}{key}/"
    )


def password_reset_confirm_redirect(request, uidb64, token):
    return HttpResponseRedirect(
        f"{settings.PASSWORD_RESET_CONFIRM_REDIRECT_BASE_URL}{uidb64}/{token}/"
    )

def is_deleted(obj):
    try:
        return obj.status == 'inactive' or not obj.is_active
    except AttributeError:
        return False

class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    callback_url = "http://localhost:8000/"
    client_class = OAuth2Client


class LogoutView(TokenBlacklistView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            return Response({"detail": _("Successfully logged out")}, status=status.HTTP_200_OK)
        return response

class CustomRegisterView(RegisterView):
    serializer_class = CustomRegisterSerializer


class RegistrationView(APIView):
    """
    Handle registration for both single restaurants and companies.
    """
    permission_classes = [AllowAny]
    async def post(self, request, *args, **kwargs):
        serializer = RegistrationSerializer(data=request.data, context={'request': request})
        if await sync_to_async(serializer.is_valid)():
            # Create either company or restaurant based on the data
            user_type = 'company' if 'company_data' in request.data else 'restaurant'
            serializer.context['role'] = 'company_admin' if user_type == 'company' else 'restaurant_owner'
            
            # Create and return the user/restaurant/company
            instance = await serializer.save()
            representation = await serializer.to_representation(instance)
            return Response(representation, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserViewSet(ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = UserSerializer
    permission_classes = [UserCreationPermission]

    async def get_queryset(self):
        # Create permission instance and set request
        permission = UserCreationPermission()
        permission.request = self.request
        return await permission.get_queryset()

    async def list(self, request, *args, **kwargs):
        queryset = await self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        serialized_data = await sync_to_async(lambda: serializer.data)()
        return Response(serialized_data)

    async def retrieve(self, request, *args, **kwargs):
        instance = await self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    async def create(self, request, *args, **kwargs):
        user = request.user
        role_to_create = request.data.get('role')

        # Validate role
        if not validate_role(role_to_create):
            return Response(
                {"detail": f"Invalid role: '{role_to_create}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check restrictions
        restrictions = {
            'CompanyAdmin': {'restaurant_owner', 'company_admin'},
            'RestaurantOwner': {'company_admin', 'restaurant_owner'},
        }
        
        # Fetch user groups asynchronously with a single query
        user_groups = {group.name async for group in user.groups.all()}
        
        # Check restrictions
        for group_name in user_groups:
            if group_name in restrictions and role_to_create.lower() in restrictions[group_name]:
                return Response(
                    {"detail": _("Cannot create {role}.").format(role=role_to_create)},
                    status=status.HTTP_403_FORBIDDEN
                )

         # Get serializer context
        context = await sync_to_async(self.get_serializer_context)()
        context['role'] = role_to_create
        
        # Initialize and validate serializer
        serializer = self.get_serializer(data=request.data, context=context)
        is_valid = await sync_to_async(serializer.is_valid)(raise_exception=True)
        
        # Create user - get validated_data synchronously
        validated_data = await sync_to_async(lambda: serializer.validated_data)()
        user = await serializer.create(validated_data)
        
        # Add to group
        await user.add_to_group(role_to_create)

        # Prepare response
        serializer.instance = user
        response_data = await sync_to_async(lambda: serializer.data)()
        email_sent = serializer.context.get("email_sent", False)
        
        return Response({**response_data, "email_sent": email_sent}, 
                    status=status.HTTP_201_CREATED)

class CompanyViewSet(ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = (ScopeAccessPolicy, DeletionPermission, )
    # CONDITIONS: 1. Already has company 2. Existing company has atleast 1 restaurant 3. Restaurant has atleast 1 branch 4. Must be CompanyAdmin
    # @TOD0: Log creation activity, add to user.companies

    @action(detail=False, methods=['get'])
    async def stats(self, request):
        count = await self.queryset.acount()
        return Response({'total_companies': count})

    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        await sync_to_async(serializer.is_valid)(raise_exception=True)
        company = await serializer.save()  # created_by is handled in serializer
        return Response(
            self.get_serializer(company).data,
            status=status.HTTP_201_CREATED
        )
    
    async def destroy(self, request, *args, **kwargs):
        # Get validated object from permission check
        pk = kwargs['pk']
        view_name = self.__class__.__name__ 
        # Fetch object for permission check
        try:
            company = await Company.objects.aget(pk=kwargs['pk'])
        except Company.DoesNotExist:
            logger.error(f'Company {pk} not found in view {view_name}')
            raise ValidationError(_("Company does not exist."))
        # Check if already deleted
        if is_deleted(company):
            raise ValidationError(_("Does Not Exist"))
        await sync_to_async(self.check_object_permissions)(request, company)

        return Response(status=204)

        
class RestaurantViewSet(ModelViewSet):
    # queryset = Restaurant.objects.filter(is_active=True)
    queryset = Restaurant.objects.filter(is_active=True)
    serializer_class = RestaurantSerializer
    permission_classes = (RestaurantAccessPolicy, RestaurantPermission, ObjectStatusPermission, DeletionPermission, )
    # permission_classes = (ScopeAccessPolicy, RestaurantPermission, ObjectStatusPermission)

    # Custom action to list all branches of a restaurant
    @action(detail=True, methods=['get'])
    def branches(self, request, pk=None):
        restaurant = self.get_object()
        branches = restaurant.branches.all()
        serializer = BranchSerializer(branches, many=True)
        return Response(serializer.data)

    # Custom action to list all employees of a restaurant
    @action(detail=True, methods=['get'])
    def employees(self, request, pk=None):
        restaurant = self.get_object()
        employees = restaurant.employees.all()
        serializer = UserSerializer(employees, many=True)
        return Response(serializer.data)

    # Custom action to retrieve the company that owns the restaurant
    @action(detail=True, methods=['get'])
    def company(self, request, pk=None):
        restaurant = self.get_object()
        company = restaurant.company
        if company:
            serializer = CompanySerializer(company)
            return Response(serializer.data)
        else:
            return Response({"detail": _("This restaurant is not associated with any company.")}, status=status.HTTP_404_NOT_FOUND)

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(ScopeAccessPolicy().get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    async def create(self, request, *args, **kwargs):
        allowed_scopes = {}
        user = request.user
        data = request.data

        user_scope = getattr(request, 'user_scope', None)
        user_groups = user_scope['groups']

        # Validation for role-based creation permissions
        if "CompanyAdmin" in user_groups:
            allowed_scopes['company'] = user_scope['company']
            
        elif "CountryManager" in user_groups:
            # CountryManager: Restricted by country and company
            allowed_scopes['country'] = user_scope['country']
            allowed_scopes['company'] = user_scope['company']

        else:
            # Other roles cannot create restaurants
            return Response({"detail": _("You do not have permission to create a restaurant.")},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            await sync_to_async(validate_scope)(user, data, allowed_scopes)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        # Pass data to serializer and save
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        restaurant = await serializer.save()

        return Response(self.get_serializer(restaurant).data, status=status.HTTP_201_CREATED)
    
    async def destroy(self, request, *args, **kwargs):
        # Get validated object from permission check
        pk = kwargs['pk']
        view_name = self.__class__.__name__ 
        # Fetch object for permission check
        try:
            restaurant = await Restaurant.objects.aget(pk=kwargs['pk'])
        except Restaurant.DoesNotExist:
            logger.error(f'Restaurant {pk} not found in view {view_name}')
            raise ValidationError(_("Restaurant does not exist."))
        # Check if already deleted
        if is_deleted(restaurant):
            raise ValidationError(_("Does Not Exist"))
        await sync_to_async(self.check_object_permissions)(request, restaurant)

        # Schedule finalization
        finalize = timezone.now() + timezone.timedelta(hours=24)
        finalize_task  = finalize_deletion.apply_async(
            args=['Restaurant', restaurant.id, request.user.id],
            eta=finalize
        )
        print(f"Scheduled finalization for Restaurant {restaurant.id} at {finalize}")

        # Offload DeletedObject creation and notifications to Celery
        handle_deletion_tasks.delay(
            object_type='Restaurant',
            object_id=restaurant.id,
            user_id=request.user.id,
            cleanup_task_id=finalize_task.id
        )
        print(f"Queued deletion tasks for Restaurant {restaurant.id}")
        
        return Response(status=204)


class BranchViewSet(ModelViewSet):
    # queryset = Branch.objects.filter(is_active=True)
    queryset = Branch.objects.filter(is_active=True)
    serializer_class = BranchSerializer
    permission_classes = (BranchAccessPolicy, BranchPermission, ObjectStatusPermission, DeletionPermission, )

    # Custom action to list all employees of a branch
    @action(detail=True, methods=['get'])
    def employees(self, request, pk=None):
        branch = self.get_object()
        employees = branch.employees.all()
        serializer = UserSerializer(employees, many=True)
        return Response(serializer.data)

    # Custom action to list all menus for a branch
    @action(detail=True, methods=['get'])
    def menus(self, request, pk=None):
        branch = self.get_object()
        menus = Menu.objects.filter(branch=branch)
        serializer = MenuSerializer(menus, many=True)     
        return Response(serializer.data)

    # Custom action to retrieve a specific menu for a branch
    @action(detail=True, methods=['get'], url_path='menus/(?P<menu_id>[^/.]+)')
    def menu_detail(self, request, pk=None, menu_id=None):
        branch = self.get_object()
        menu = get_object_or_404(Menu, id=menu_id, branch=branch)
        categories = MenuCategory.objects.filter(menu=menu).prefetch_related('menu_items')
        data = {
            "menu": MenuSerializer(menu).data,
            "categories": MenuCategorySerializer(categories, many=True).data
        }
        return Response(data)

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(ScopeAccessPolicy().get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    async def create(self, request, *args, **kwargs):
        allowed_scopes = {}
        user = request.user
        data = request.data
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)

        user_scope = getattr(request, 'user_scope', None)
        user_groups = user_scope['groups']
        print("user_scope: ", user_scope)

        # Validation for role-based creation permissions
        if "CompanyAdmin" in user_groups:
            allowed_scopes['company'] = user_scope['company']
            allowed_scopes['restaurant'] = user_scope['restaurant']
            serializer.context['is_CEO'] = True

        elif "CountryManager" in user_groups:
            allowed_scopes['country'] = user_scope['country']
            allowed_scopes['company'] = user_scope['company']
            allowed_scopes['restaurant'] = user_scope['restaurant']

        elif "RestaurantOwner" in user_groups:
            allowed_scopes['restaurant'] = user_scope['restaurant']
            serializer.context['is_CEO'] = True

        elif "RestaurantManager" in user_groups:
            allowed_scopes['restaurant'] = user_scope['restaurant']
        else:
            # Other roles cannot create restaurants
            return Response({"detail": _("You do not have permission to create a branch.")},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            await sync_to_async(validate_scope)(user, data, allowed_scopes)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        # Pass data to serializer and save
        branch = await serializer.save()

        return Response(self.get_serializer(branch).data, status=status.HTTP_201_CREATED)
    
    async def destroy(self, request, *args, **kwargs):
        # Get validated object from permission check
        pk = kwargs['pk']
        view_name = self.__class__.__name__ 
        # Fetch object for permission check
        try:
            branch = await Branch.objects.aget(pk=kwargs['pk'])
        except Branch.DoesNotExist:
            logger.error(f'Branch {pk} not found in view {view_name}')
            raise ValidationError(_("Branch does not exist."))
        app_label = branch._meta.app_label
        model_name = branch.__class__.__name__
        # Check if already deleted
        if is_deleted(branch):
            raise ValidationError(_("Does Not Exist"))

        await sync_to_async(self.check_object_permissions)(request, branch)
        
        # Schedule finalization
        finalize = timezone.now() + timezone.timedelta(minutes=2)
        finalize_task  = finalize_deletion.apply_async(
            args=[f'{app_label}.{model_name}', branch.id, request.user.id],
            eta=finalize
        )
        print(f"Scheduled finalization for Branch {branch.id} at {finalize}")

        # Offload DeletedObject creation and notifications to Celery
        handle_deletion_tasks.delay(
            object_type=f'{app_label}.{model_name}',
            object_id=branch.id,
            user_id=request.user.id,
            cleanup_task_id=finalize_task.id,
            finalize=finalize
        )
        print(f"Queued deletion tasks for Branch {branch.id}")

        return Response(status=204)

class MenuViewSet(ModelViewSet):
    """
    ViewSet for managing Menu objects.
    Provides CRUD operations for Menu model.
    """
    queryset = Menu.objects.all()  # Retrieve all Menu objects
    serializer_class = MenuSerializer  # Use MenuSerializer for serialization


class MenuCategoryViewSet(ModelViewSet):
    """
    ViewSet for managing MenuCategory objects.
    Provides CRUD operations for MenuCategory model.
    """
    queryset = MenuCategory.objects.all() 
    serializer_class = MenuCategorySerializer 


class MenuItemViewSet(ModelViewSet):
    """
    ViewSet for managing MenuItem objects.
    Provides CRUD operations for MenuItem model.
    """
    queryset = MenuItem.objects.all() 
    serializer_class = MenuItemSerializer  


class OrderModifyView(APIView):
    """
    API endpoint for modifying orders.
    Supports adding and removing items from an existing order.
    Uses optimistic locking to prevent concurrent modifications.
    """

    async def put(self, request, order_id):
        """
        Handles PUT requests for order modifications.
        
        Args:
            request (Request): The HTTP request object.
            order_id (int): The ID of the order to modify.
        
        Returns:
            Response: JSON response with updated order details or error message.
            {
                "version": 6,
                "total_price": "45.00"
            }

        Accepts:
            {
                "expected_version": 5,
                "changes": [
                    {"action": "add", "menu_item": 42, "quantity": 2},
                    {"action": "remove", "order_item": 789}
                ]
            }
        """
        try:
            # Start an atomic transaction to ensure data consistency
            with transaction.atomic():
                # Lock the order row to prevent concurrent modifications
                order = await Order.objects.select_for_update().aget(id=order_id)
                
                # Check for version mismatch (optimistic locking)
                if order.version != request.data.get('expected_version'):
                    return Response(
                        {"error": "Concurrent modification detected"},
                        status=status.HTTP_409_CONFLICT
                    )
                
                # Process each change in the request
                for change in request.data.get('changes', []):
                    if change['action'] == 'add':
                        # Add a new item to the order
                        await OrderItem.objects.acreate(
                            order=order,
                            menu_item_id=change['menu_item'],
                            quantity=change['quantity'],
                            item_price=await MenuItem.objects.aget(id=change['menu_item']).price
                        )
                    elif change['action'] == 'remove':
                        # Remove an item from the order
                        await OrderItem.objects.filter(id=change['order_item']).adelete()
                
                # Refresh the order object to reflect changes
                await order.arefresh_from_db()
                
                # Return the updated order version and total price
                return Response({
                    "version": order.version,
                    "total_price": order.total_price
                }, status=status.HTTP_200_OK)
        
        except Order.DoesNotExist:
            # Handle case where order does not exist
            return Response(
                {"error": "Order not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        except MenuItem.DoesNotExist:
            # Handle case where menu item does not exist
            return Response(
                {"error": "Invalid menu item"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        except Exception as e:
            # Handle unexpected errors
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class ShiftViewSet(ModelViewSet):
    """
    Async ViewSet for managing shift templates.
    Endpoint: POST /api/shifts/
    """
    serializer_class = ShiftSerializer
    permission_classes = (ScopeAccessPolicy, ShiftPermission, )
    queryset = Shift.objects.all()

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(ScopeAccessPolicy().get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    async def create(self, request, *args, **kwargs):
        """Create a shift with Redis caching."""
        data = request.data.copy()
        data['branch_id'] = int(request.data['branches'][0])
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        shift = Shift(**serializer.validated_data)
        await shift.asave()
        # Invalidate branch shifts cache

        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
class StaffShiftViewSet(ModelViewSet):
    """
    Endpoint: POST /api/staff-shifts/
    {
        "user": 101,
        "shift": 1,
        "date": "2025-05-06"
    }
    """
    queryset = StaffShift.objects.all()
    serializer_class = StaffShiftSerializer
    permission_classes = (ScopeAccessPolicy, )

    async def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return self.queryset
        return self.queryset.filter(user=user)

    @action(detail=False, methods=["post"], url_path="reassign")
    async def reassign(self, request):
        """
        Endpoint: POST /api/staff-shifts/reassign/
        {
            "staff_shift_id": 1,
            "shift_id": 2,
            "date": "2025-05-06"
        }
        """
        staff_shift_id = request.data.get("staff_shift_id")
        new_shift_id = request.data.get("shift_id")
        date = request.data.get("date", timezone.now().date())

        staff_shift = await StaffShift.objects.aget(id=staff_shift_id)
        new_shift = await Shift.objects.aget(id=new_shift_id)

        if staff_shift.shift.branch_id != new_shift.branch_id:
            return Response({"error": "Shifts must belong to the same branch"}, status=status.HTTP_400_BAD_REQUEST)
        if not await request.user.branches.filter(id=new_shift.branch_id).aexists():
            return Response({"error": "No permission for this branch"}, status=status.HTTP_403_FORBIDDEN)

        from notifications.tasks import log_shift_assignment
        with transaction.atomic():
            staff_shift.shift_id = new_shift_id
            staff_shift.date = date
            await staff_shift.asave()

            log_shift_assignment.delay(
                branch_id=new_shift.branch_id,
                user_id=staff_shift.user_id,
                shift_id=new_shift_id,
                date=date,
                action="reassign"
            )

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"user_{staff_shift.user_id}",
            {
                "type": "shift_notification",
                "message": f"Your shift on {date} has been reassigned to {new_shift.name}.",
            }
        )

        return Response({"status": "Shift reassigned"}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="upcoming")
    async def upcoming(self, request):
        user = request.user
        queryset = self.queryset.filter(
            user=user,
            date__gte=timezone.now().date(),
            date__lte=timezone.now().date() + timezone.timedelta(days=7)
        )
        serializer = self.get_serializer(await queryset.alist(), many=True)
        return Response(serializer.data)
    
class ShiftPatternViewSet(ModelViewSet):
    """
    Endpoint: POST /api/shift-patterns/
    """
    queryset = ShiftPattern.objects.all()
    serializer_class = ShiftPatternSerializer
    permission_classes = (ScopeAccessPolicy, ShiftPatternPermission, )

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(ScopeAccessPolicy().get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    @action(
        detail=True,
        methods=["post"],
        url_path="regenerate",
        # permission_classes=(ScopeAccessPolicy,)  # Only ScopeAccessPolicy
    )
    async def regenerate(self, request, pk=None):
        """
        Endpoint: POST /api/shift-patterns/1/regenerate/
        Request: {} (empty body)k
        """
        pattern_id = int(request.pattern_id)
        await ShiftUpdateHandler.handle_pattern_change(pattern_id)
        return Response({"status": _("Shift regeneration queued")}, status=status.HTTP_202_ACCEPTED)

class OvertimeRequestViewSet(ModelViewSet):
    """API for overtime requests."""
    queryset = OvertimeRequest.objects.all()
    serializer_class = OvertimeRequestSerializer
    # permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        """User creates an overtime request."""
        serializer.save(staff_shift=StaffShift.objects.get(
            user=self.request.user,
            date=timezone.now().date()
        ))

    @action(detail=True, methods=['post'], url_path='approve')
    def approve_overtime(self, request, pk=None):
        """Manager approves an overtime request."""
        # if not request.user.is_staff:
        #     return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)
        ot_request = self.get_object()
        ot_request.approve()
        # Notify user via WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"user_{ot_request.staff_shift.user.id}",
            {
                'type': 'overtime_notification',
                'message': 'Your overtime request has been approved.'
            }
        )
        return Response({'status': 'Approved'}, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'], url_path='extend-overtime')
    async def extend_overtime(self, request):
        """Manager extends overtime for one or multiple users."""
        user_ids = request.data.get('user_ids', [])  # List of user IDs
        hours = request.data.get('hours', 1.0)
        if not user_ids:
            return Response({'error': 'No users specified'}, status=status.HTTP_400_BAD_REQUEST)

        # Filter shifts by user's manageable branches
        shifts = await StaffShift.objects.filter(
            user__id__in=user_ids,
            shift__branch__in=request.user.branches.all(),
            end_datetime__gte=timezone.now()
        )
        if not await shifts.aexists():
            return Response({'error': 'No valid shifts found'}, status=status.HTTP_404_NOT_FOUND)

        channel_layer = get_channel_layer()
        for shift in shifts:
            # Permission already checked by BranchRolePermission
            shift.extend_overtime(hours)
            channel_layer.group_send(
                f"user_{shift.user.id}",
                {
                    'type': 'overtime_notification',
                    'message': f"Your shift has been extended by {hours} hours."
                }
            )
        return Response({'status': 'Overtime extended'}, status=status.HTTP_200_OK)