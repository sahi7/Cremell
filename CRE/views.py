from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db import transaction

from rest_framework.response import Response
# from rest_framework.views import APIView
# from rest_framework.viewsets import ModelViewSet
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

from .serializers import UserSerializer, CustomRegisterSerializer, RegistrationSerializer, RestaurantSerializer, BranchSerializer, BranchMenuSerializer, MenuSerializer, MenuCategorySerializer
from .serializers import MenuItemSerializer, CompanySerializer, StaffShiftSerializer, OvertimeRequestSerializer
from .models import Company, Restaurant, Branch, Menu, MenuItem, MenuCategory, Order, OrderItem, Shift, StaffShift, StaffAvailability, OvertimeRequest
from zMisc.policies import RestaurantAccessPolicy, BranchAccessPolicy, ScopeAccessPolicy
from zMisc.permissions import UserCreationPermission, RManagerScopePermission, BManagerScopePermission, ObjectStatusPermission
from zMisc.utils import validate_scope, filter_queryset_by_scopes, get_scope_filters

CustomUser = get_user_model()
def email_confirm_redirect(request, key):
    return HttpResponseRedirect(
        f"{settings.EMAIL_CONFIRM_REDIRECT_BASE_URL}{key}/"
    )


def password_reset_confirm_redirect(request, uidb64, token):
    return HttpResponseRedirect(
        f"{settings.PASSWORD_RESET_CONFIRM_REDIRECT_BASE_URL}{uidb64}/{token}/"
    )


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
        user = self.request.user
        scope_filter, min_role_value = await sync_to_async(get_scope_filters)(user)
        queryset = CustomUser.objects.filter(scope_filter)
        return queryset.filter(
            id__in=await sync_to_async(
                lambda: [u.id for u in queryset if user.get_role_value(u.role) >= min_role_value]
            )()
        )

    async def list(self, request, *args, **kwargs):
        queryset = await self.get_queryset()
        serializer = self.get_serializer(
            await sync_to_async(lambda: list(queryset))(),
            many=True
        )
        data = await sync_to_async(lambda: serializer.data)()
        return Response(data)

    async def create(self, request, *args, **kwargs):
        user = request.user
        role_to_create = request.data.get('role')

        # Validate role
        available_roles = {role for role, _ in CustomUser.ROLE_CHOICES}
        if not role_to_create or role_to_create not in available_roles:
            return Response(
                {"detail": f"Invalid role: '{role_to_create}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check restrictions
        restrictions = {
            'CompanyAdmin': 'restaurant_owner',
            'RestaurantOwner': 'company_admin',
        }
        
        for group_name, restricted_role in restrictions.items():
            if await sync_to_async(user.groups.filter(name=group_name).exists)():
                if role_to_create == restricted_role:
                    return Response(
                        {"detail": f"{group_name} cannot create {restricted_role}."},
                        status=status.HTTP_403_FORBIDDEN
                    )

        # Check hierarchy
        if not await sync_to_async(user.groups.filter(name="CompanyAdmin").exists)():
            user_role_value = await sync_to_async(user.get_role_value)()
            role_to_create_value = await sync_to_async(user.get_role_value)(role_to_create)
            if role_to_create_value <= user_role_value:
                return Response(
                    {"detail": _("Cannot create user with higher/equal role.")},
                    status=status.HTTP_400_BAD_REQUEST
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
        await sync_to_async(user.add_to_group)(role_to_create)

        # Prepare response
        serializer.instance = user
        response_data = await sync_to_async(lambda: serializer.data)()
        email_sent = serializer.context.get("email_sent", False)
        
        return Response({**response_data, "email_sent": email_sent}, 
                    status=status.HTTP_201_CREATED)

class CompanyViewSet(ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    # CONDITIONS: 1. Already has company 2. Existing company has atleast 1 restaurant 3. Restaurant has atleast 1 branch 4. Must be CompanyAdmin
    # @TOD0: Log creation activity, add to user.companies

    @action(detail=False, methods=['get'])
    async def stats(self, request):
        count = await sync_to_async(self.queryset.count)()
        return Response({'total_companies': count})

    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        await sync_to_async(serializer.is_valid)(raise_exception=True)
        company = await serializer.save()  # created_by is handled in serializer
        return Response(
            self.get_serializer(company).data,
            status=status.HTTP_201_CREATED
        )

        
class RestaurantViewSet(ModelViewSet):
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    permission_classes = (RestaurantAccessPolicy, RManagerScopePermission, ObjectStatusPermission)
    # permission_classes = (ScopeAccessPolicy, RManagerScopePermission, ObjectStatusPermission)

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
        user = request.user

        # Define allowed scopes for the user
        allowed_scopes = {}

        # Get data from the request
        data = request.data

        # Validation for role-based creation permissions
        if await sync_to_async(user.groups.filter(name="CompanyAdmin").exists)():
            allowed_scopes['company'] = await sync_to_async(user.companies.values_list)('id', flat=True)
            

        elif await sync_to_async(user.groups.filter(name="CountryManager").exists)():
            # CountryManager: Restricted by country and company
            allowed_scopes['country'] = await sync_to_async(user.countries.values_list)('id', flat=True)
            allowed_scopes['company'] = await sync_to_async(user.companies.values_list)('id', flat=True)

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


class BranchViewSet(ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    permission_classes = (BranchAccessPolicy, ObjectStatusPermission, BManagerScopePermission,)

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
        user = request.user

        # Define allowed scopes for the user
        allowed_scopes = {}

        # Get data from the request
        data = request.data
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)

        # Validation for role-based creation permissions
        user_groups = await sync_to_async(lambda: set(user.groups.values_list('name', flat=True)))()
        if "CompanyAdmin" in user_groups:
            allowed_scopes['company'] = await sync_to_async(lambda: user.companies.values_list('id', flat=True))()
            allowed_scopes['restaurant'] = await sync_to_async(lambda: user.restaurants.filter(company_id__in=allowed_scopes['company']).values_list('id', flat=True))()

        elif "CountryManager" in user_groups:
            allowed_scopes['country'] = await sync_to_async(lambda: user.countries.values_list('id', flat=True))()
            allowed_scopes['company'] = await sync_to_async(lambda: user.companies.values_list('id', flat=True))()
            allowed_scopes['restaurant'] = await sync_to_async(lambda: user.restaurants.filter(company_id__in=allowed_scopes['company']).values_list('id', flat=True))()

        elif "RestaurantOwner" in user_groups:
            allowed_scopes['restaurant'] = await sync_to_async(lambda: user.restaurants.values_list('id', flat=True))()
            serializer.context['is_restaurant_owner'] = True

        elif "RestaurantManager" in user_groups:
            allowed_scopes['restaurant'] = await sync_to_async(lambda: user.restaurants.values_list('id', flat=True))()
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

    def put(self, request, order_id):
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
                order = Order.objects.select_for_update().get(id=order_id)
                
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
                        OrderItem.objects.create(
                            order=order,
                            menu_item_id=change['menu_item'],
                            quantity=change['quantity'],
                            item_price=MenuItem.objects.get(id=change['menu_item']).price
                        )
                    elif change['action'] == 'remove':
                        # Remove an item from the order
                        OrderItem.objects.filter(id=change['order_item']).delete()
                
                # Refresh the order object to reflect changes
                order.refresh_from_db()
                
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


class StaffShiftViewSet(ModelViewSet):
    """API for managing staff shifts."""
    queryset = StaffShift.objects.all()
    serializer_class = StaffShiftSerializer
    # permission_classes = [permissions.IsAuthenticated]

    # def get_queryset(self):
    #     """Filter by user or manager role."""
    #     if self.request.user.is_staff:  # Assuming managers have is_staff=True
    #         return self.queryset
    #     return self.queryset.filter(user=self.request.user)

    @action(detail=False, methods=['post'], url_path='extend-overtime')
    def extend_overtime(self, request):
        """Manager extends overtime for one or multiple users."""
        user_ids = request.data.get('user_ids', [])  # List of user IDs
        hours = request.data.get('hours', 1.0)
        if not user_ids:
            return Response({'error': 'No users specified'}, status=status.HTTP_400_BAD_REQUEST)

        # Filter shifts by user's manageable branches
        shifts = StaffShift.objects.filter(
            user__id__in=user_ids,
            shift__branch__in=request.user.branches.all(),
            end_datetime__gte=timezone.now()
        )
        if not shifts.exists():
            return Response({'error': 'No valid shifts found'}, status=status.HTTP_404_NOT_FOUND)

        channel_layer = get_channel_layer()
        for shift in shifts:
            # Permission already checked by BranchRolePermission
            shift.extend_overtime(hours)
            async_to_sync(channel_layer.group_send)(
                f"user_{shift.user.id}",
                {
                    'type': 'overtime_notification',
                    'message': f"Your shift has been extended by {hours} hours."
                }
            )
        return Response({'status': 'Overtime extended'}, status=status.HTTP_200_OK)

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