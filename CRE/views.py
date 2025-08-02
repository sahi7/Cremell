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
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework_simplejwt.views import TokenBlacklistView

from dj_rest_auth.registration.views import SocialLoginView
from dj_rest_auth.registration.views import RegisterView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync, sync_to_async 
from adrf.views import APIView
from adrf.viewsets import ModelViewSet

from .serializers import *
from .models import *
from .tasks import create_staff_availability

from archive.tasks import finalize_deletion, handle_deletion_tasks
from notifications.tasks import log_shift_assignment
from zMisc.policies import RestaurantAccessPolicy, BranchAccessPolicy, ScopeAccessPolicy

from zMisc.permissions import *
from zMisc.utils import validate_scope, validate_role, clean_request_data
from zMisc.shiftresolver import ShiftUpdateHandler
from zMisc.atransactions import aatomic
from services.sequences import generate_order_number
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
        _scopes = await get_scopes_and_groups(user)
        user_groups = _scopes['groups']
        
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

        create_staff_availability.delay(user.id)

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
    queryset = Menu.objects.filter(is_active=True)  # Retrieve all Menu objects 
    serializer_class = MenuSerializer  # Use MenuSerializer for serialization

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, MenuPermission()]
    
    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    async def create(self, request, *args, **kwargs):
        """
        Override create to modify request data and use async serializer.
        Sets branch_id to the first branch in request.data['branches'].
        """
        # Create a mutable copy of request.data
        if 'branch' in request.data:
            del request.data['branch'] 
        data = request.data.copy()
        # Modify branch_id to use the first branch from branches list
        if 'branches' in data and data['branches']:
            data['branch'] = int(data['branches'][0])
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        instance = await serializer.save()
        return Response(serializer.to_representation(instance), status=status.HTTP_201_CREATED)


class MenuCategoryViewSet(ModelViewSet):
    """
    ViewSet for managing MenuCategory objects.
    Provides CRUD operations for MenuCategory model.
    """
    queryset = MenuCategory.objects.filter(is_active=True) 
    serializer_class = MenuCategorySerializer 

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, MenuCategoryPermission()]
    
    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)
    
    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        instance = await serializer.save()
        return Response(serializer.to_representation(instance), status=status.HTTP_201_CREATED)


class MenuItemViewSet(ModelViewSet):
    """
    ViewSet for managing MenuItem objects.
    Provides CRUD operations for MenuItem model.
    """
    queryset = MenuItem.objects.filter(is_active=True) 
    serializer_class = MenuItemSerializer  

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, MenuItemPermission()]
    
    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)
    
    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        instance = await serializer.save()
        return Response(serializer.to_representation(instance), status=status.HTTP_201_CREATED)


from decimal import Decimal
from CRE.tasks import send_to_kds
from services.sequences import generate_order_number
class OrderViewSet(ModelViewSet):
    """
    API endpoint for CRUD orders.
    Supports adding and removing items from an existing order.
    Uses optimistic locking to prevent concurrent modifications.
    """
    queryset = Order.objects.filter(is_active=True)
    serializer_class = OrderSerializer

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, OrderPermission(),]
    
    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)
    
    async def get_valid_menu_item_ids(self, branch):
        menu_data = await branch.get_menus()
        valid_menu_item_ids = set()
        for menu_id, item_ids in menu_data.items():
            valid_menu_item_ids.update(item_ids)
        return valid_menu_item_ids

    async def create(self, request, *args, **kwargs):
        """
        Async Order Creation Endpoint
        Example Payload:
        {
            "branches": [1],
            "order_type": "dine_in",
            "table_number": "A12",
            "items": [
                {"menu_item": 1, "quantity": 2, "special_requests": "No onions"},
                {"menu_item": 2, "quantity": 1, "course": "main"}  # Added course timing
            ],
            "notes": "No onions please"
        }
        """

        # Initial Validation
        try:
            if 'branch' in request.data:
                del request.data['branch'] 
            data = request.data.copy()
            data['branch'] = int(request.data['branches'][0])
            serializer = OrderSerializer(data=data)
            await sync_to_async(serializer.is_valid)(raise_exception=True)
            validated_data = serializer.validated_data
        except KeyError as e:
            raise ValidationError(f'Error on the field {e}')

        # @aatomic()
        async def create_order():
            try:
                # 1. Get Branch with lock
                # branch = await Branch.objects.select_for_update().aget(id=data['branch'])
                branch = await Branch.objects.prefetch_related('country').aget(id=data['branch'])
                user = request.user

                # 2. Get menus and validate menu items
                valid_menu_item_ids = await self.get_valid_menu_item_ids(branch)
                print("valid_menu_item_ids: ", valid_menu_item_ids)

                # 3. Validate menu items belong to branch
                item_ids = [item['menu_item'].id for item in validated_data['items']]
                invalid_items = set(item_ids) - valid_menu_item_ids
                if invalid_items:
                    logger.error(f"Validation failed: Menu items {invalid_items} do not belong to branch {branch.id}")
                    return Response(
                        {"error": f"Menu items {invalid_items} are invalid or are unavailable."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if len(item_ids) != len(set(item_ids)):
                    return Response(
                        {"error": _("Duplicate menu items are not allowed in the order.")},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                # 4. Calculate Total Price
                total_price = Decimal('0.00')
                menu_items = []
                item_names = [(item['menu_item'].name) for item in validated_data['items']]
                print("item_names: ", item_names)
                # print("item_ids: ", item_ids)
                items_map = {item.id: item async for item in MenuItem.objects.filter(id__in=item_ids)}
                # print("items_map: ", items_map)
                
                # Calculate total price and prepare items
                for item_data in validated_data['items']:
                    menu_item = items_map[item_data['menu_item'].id ]
                    quantity = item_data['quantity']
                    item_price = menu_item.price * quantity
                    total_price += item_price
                    
                    menu_items.append({
                        'menu_item': menu_item,
                        'quantity': quantity,
                        'price': item_price
                    })
                # print("total_price: ", total_price)
                # print("menu_items: ", menu_items)
                # print("validated_data: ", validated_data)

                # 5. Create Order with Final Price
                filtered_validated_data = {
                    k: v for k, v in validated_data.items()
                    if k not in ['branch', 'items']
                }
                order = await Order.objects.acreate(
                    branch=branch,
                    order_number=await generate_order_number(branch),
                    source=validated_data.get('source', 'web'),
                    total_price=total_price,  # Now has the correct calculated value
                    created_by=user,
                    special_instructions=data.get('notes', ''),
                    status='received',  # Ensure default status is set
                    **filtered_validated_data
                )

                # 6. Bulk Create Items
                await OrderItem.objects.abulk_create([
                    OrderItem(
                        order=order,
                        menu_item=item['menu_item'],
                        quantity=item['quantity'],
                        item_price=item['price'],
                        added_by=user,
                    ) for item in menu_items
                ])

                # 7. Fire notification and forget
                details = {
                    'user_id': request.user.id,
                    'item_names': item_names,
                }
                send_to_kds.delay(order.id, details)

                # serializer = OrderSerializer(order)
                serialized_data = await sync_to_async(lambda: serializer.data)()
                return Response(serialized_data, status=status.HTTP_201_CREATED)

               
            except KeyError as e:
                logger.error(f"Missing key in order data: {str(e)}")
                return Response(
                    {"error": f"Missing required field: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
                # asyncio.create_task(
                #     update_inventory_levels.delay([i.menu_item_id for i in order_items])
                # )
                
            except ValidationError as e:
                return Response(
                    {"error": str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                logger.error(f"Order creation failed: {str(e)}")
                return Response(
                    {"error": _("Internal server error")},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        response = await create_order()
        return response
        


    @action(detail=True, methods=['patch'], url_path='modify')
    async def order_modify(self, request, *args, **kwargs):
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
        change just quantity:
            {"action": "add", "menu_item": 42, "quantity": -2}, Reduce initial qty by 2 units
        """
        pk = kwargs['pk']
        # @aatomic
        async def modify_order():
            try:
                # Start an atomic transaction to ensure data consistency
                # with transaction.atomic():
                    # 1. Lock and get order
                    # Lock the order row to prevent concurrent modifications
                    # order = await Order.objects.select_for_update().aget(id=pk)
                data = request.data
                order = await Order.objects.select_related('branch').aget(id=pk)
                order_v = order.version
                changes = data.get('changes', [])
                total_price = order.total_price
                
                # 2. Check version
                # Check for version mismatch (optimistic locking)
                if not data.get('expected_version'):
                    return Response(
                        {"error": _("Order version mismatch -- Order must have a version")},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if order_v != data['expected_version']:
                    return Response(
                        {"error": _("Concurrent modification detected")},
                        status=status.HTTP_409_CONFLICT
                    )
                
                # 3. Process changes
                # Process each change in the request
                if changes:
                    # Prepare bulk operations
                    items_to_create = []
                    item_ids_to_delete = []
                    items_to_update = []
                    menu_item_ids = set()

                    # Collect menu_item IDs for add actions and map existing OrderItems
                    for change in changes:
                        if change['action'] == 'add':
                            menu_item_ids.add(change['menu_item'])
                        elif change['action'] == 'remove':
                            item_ids_to_delete.append(change['order_item'])

                    valid_menu_item_ids = await self.get_valid_menu_item_ids(order.branch)

                    invalid_items = menu_item_ids - valid_menu_item_ids
                    if invalid_items:
                        logger.error(f"Validation failed: Menu items {invalid_items} ")
                        return Response(
                            {"error": f"Menu items {invalid_items} are invalid or are unavailable."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    
                    # Bulk fetch MenuItems
                    menu_items = {item.id: item async for item in MenuItem.objects.filter(id__in=menu_item_ids).all()}
                    item_names = [menu_items[menu_item_id].name for menu_item_id in menu_item_ids if menu_item_id in menu_items]
                    
                    # Bulk fetch existing OrderItems for the order
                    existing_items = {item.menu_item_id: item async for item in OrderItem.objects.filter(order=order).select_related('menu_item').all()}
                    for change in changes:
                        if change['action'] == 'add':
                            menu_item = menu_items.get(change['menu_item'])
                            if not menu_item:
                                logger.warning(f"MenuItem {change['menu_item']} not found")
                                continue
                            item_price = menu_item.price * change['quantity']
                            
                            existing_order_item = existing_items.get(change['menu_item'])
                            if existing_order_item:
                                # Update existing OrderItem
                                old_item_price = existing_order_item.item_price
                                new_quantity = existing_order_item.quantity + change['quantity']
                                existing_order_item.quantity = new_quantity
                                existing_order_item.item_price = menu_item.price * new_quantity
                                existing_order_item.added_by = request.user
                                items_to_update.append(existing_order_item)
                                total_price = total_price - old_item_price + existing_order_item.item_price
                                logger.debug(f"Prepared update for OrderItem: menu_item={menu_item.id}, new_quantity={new_quantity}, new_item_price={existing_order_item.item_price}")
                            else:
                                # Create new OrderItem
                                items_to_create.append(OrderItem(
                                    order=order,
                                    menu_item=menu_item,
                                    quantity=change['quantity'],
                                    item_price=item_price,
                                    added_by=request.user
                                ))
                                total_price += item_price
                                logger.debug(f"Prepared create for OrderItem: menu_item={menu_item.id}, quantity={change['quantity']}, item_price={item_price}")
                        elif change['action'] == 'remove':
                            order_item = existing_items.get(change['order_item'])
                            if order_item:
                                total_price -= order_item.item_price
                                logger.debug(f"Prepared delete for OrderItem: order_item={change['order_item']}, item_price={order_item.item_price}")
                            else:
                                logger.warning(f"OrderItem {change['order_item']} not found for order {order.id}")

                    # Bulk create OrderItems
                    if items_to_create:
                        await OrderItem.objects.abulk_create(items_to_create)
                        logger.debug(f"Bulk created {len(items_to_create)} OrderItems")

                    # Bulk update OrderItems
                    if items_to_update:
                        fields_to_update = ['quantity', 'item_price', 'added_by']
                        await sync_to_async(lambda: OrderItem.objects.bulk_update(items_to_update, fields_to_update))()
                        logger.debug(f"Bulk updated {len(items_to_update)} OrderItems")

                    # Bulk delete OrderItems
                    if item_ids_to_delete:
                        deleted = await OrderItem.objects.filter(id__in=item_ids_to_delete, order=order).adelete()
                        logger.debug(f"Bulk deleted {deleted[0]} OrderItems")

                    # Ensure total_price is not negative
                    if total_price < 0:
                        logger.error(f"Negative total_price calculated for order {order.id}: {total_price}")
                        raise ValidationError("Total price cannot be negative")
                
                # 4. Refresh order data
                # Refresh the order object to reflect changes
                for field, value in data.items():
                    if hasattr(order, field):
                        setattr(order, field, value)
                order.version += 1
                order.total_price = total_price
                order.special_instructions = data.get('notes')
                await order.asave()
                await order.arefresh_from_db()

                details = {
                    'user_id': request.user.id,
                    'menu_item_names': item_names,
                }
                send_to_kds.delay(order.id, details)
                
                # 5. Prepare success response (LAST THING WE DO)
                serializer = OrderSerializer(order)
                serialized_data = await sync_to_async(lambda: serializer.data)()
                return Response(serialized_data, status=status.HTTP_200_OK)
                    
            
            except Order.DoesNotExist:
                # Handle case where order does not exist
                return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)
            
            except MenuItem.DoesNotExist:
                # Handle case where menu item does not exist
                return Response({"error": "Invalid menu item"}, status=status.HTTP_400_BAD_REQUEST)
            
            except Exception as e:
                # Handle unexpected errors
                logger.error(f"Order creation failed: {str(e)}", exc_info=True)
                return Response({"error": "Order processing failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 6. FINAL STEP: Return response
        response = await modify_order()
        return response
        

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
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
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

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, StaffShiftPermission()]

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    @action(detail=True, methods=["post"], url_path="reassign")
    async def reassign(self, request, pk=None):
        """
        Endpoint: POST /api/staff-shifts/{pk}/reassign/
        {
            "shift_id": 2,
            "date": "2025-05-06"
        }
        """
        staff_shift = request.staff_shift
        original_user_id = staff_shift.user_id
        original_date = staff_shift.date
        date = getattr(request, 'date', None)
        shift_id = getattr(request, 'shift_id', None)
        user_id = getattr(request, 'user_id', None)
        original_shift_name = await sync_to_async(lambda: staff_shift.shift.name)()

        # @aatomic()
        async def run_atomic(new_shift_name):        
            staff_shift.shift_id = shift_id
            staff_shift.date = date
            staff_shift.user_id = user_id
            await staff_shift.asave(update_fields=['user_id', 'date', 'shift_id'])
            # print("in @aatomic(): ", staff_shift.date)

            new_user_id = staff_shift.user_id
            new_date = staff_shift.date
            
            # Track what actually changed
            changes = {
                'user': new_user_id != original_user_id,
                'date': new_date != original_date,
                'shift': shift_id != staff_shift.shift_id
            }
            # print("orig id - New id: ", original_user_id, new_user_id)
            # print("orig name - New name: ", original_shift_name, new_shift_name)
            # print("orig date - New date: ", original_date, new_date)

            log_shift_assignment.delay(
                branch_id=staff_shift.branch_id,
                user_id=staff_shift.user_id,
                shift_id=shift_id,
                date=date,
                action="reassign",
                original_user_id=original_user_id,
                new_user_id=new_user_id,
                original_shift_name=original_shift_name,
                new_shift_name=new_shift_name,
                original_date=original_date,
                new_date=new_date,
                changes=changes
            )
        new_shift_name = await sync_to_async(lambda: staff_shift.shift.name)()
        await run_atomic(new_shift_name)


        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"user_{staff_shift.user_id}",
            {
                "type": "shift_notification",
                "message": f"Your shift on {date} has been reassigned to {new_shift_name}.",
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
    
class ShiftSwapRequestViewSet(ModelViewSet):
    """
    Request body:
    {
        "initiator_shift": 123,
        "desired_date": "2025-08-10"
    }
    """
    queryset = ShiftSwapRequest.objects.all()
    serializer_class = ShiftSwapRequestSerializer

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, ShiftSwapPermission(),]
    
    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    async def create(self, request, *args, **kwargs):
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch'] = request.branch
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        validated_data = serializer.validated_data
        validated_data['initiator'] = request.user
        shift_swap_request = ShiftSwapRequest(**validated_data)
        await shift_swap_request.asave()
        branch_id = shift_swap_request.branch_id

        # Log activity
        details = f"Requested swap for shift {shift_swap_request.initiator_shift_id} on {shift_swap_request.desired_date}"
        log_activity.delay(request.user.id, 'shift_swap_request', details, branch_id, 'branch')

        # Send WebSocket notification
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"{branch_id}_{request.user.role}",
            {
                'signal': 'shift_swap_request',
                'type': 'branch.update',
                'message': {
                    'id': shift_swap_request.id,
                    'initiator': request.user.username,
                    'shift_id': shift_swap_request.initiator_shift.id,
                    'desired_date': shift_swap_request.desired_date.isoformat(),
                }
            }
        )

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    async def accept(self, request, pk=None):
        # Update swap request
        swap_request = request.swap_request
        swap_request.counterparty_id = request.user.id
        if swap_request.counterparty == swap_request.initiator:
            return Response(
                        {"error": _("Concurrent modification detected")},
                        status=status.HTTP_409_CONFLICT
                    )
        swap_request.counterparty_shift_id = request.counterparty_shift.shift_id
        swap_request.status = 'completed'
        swap_request.accepted_at = timezone.now()
        await swap_request.asave(update_fields=['status', 'accepted_at', 'counterparty_shift_id', 'counterparty_id'])

        # Update shifts
        initiator_saff_shift = await StaffShift.objects.aget(
            user_id=swap_request.initiator_id,  # Ensures initiator owns the shift
            date=swap_request.desired_date
            # is_swappable=True
        )
        initiator_shift = swap_request.initiator_shift
        counterparty_shift = request.counterparty_shift.shift

        counterparty_staff_shift_id = request.counterparty_shift.shift_id
        initiator_staff_shift_id = initiator_saff_shift.shift_id

        print(f"\n=== STAFF SHIFT ID MAPPING ===")
        print(f"Original Counterparty Staff Shift ID: {counterparty_staff_shift_id}")
        print(f"Original Initiator Staff Shift ID: {initiator_staff_shift_id}")

        counterparty_staff_shift_id_swaped = initiator_staff_shift_id
        initiator_staff_shift_id_swaped = counterparty_staff_shift_id

        print(f"\n=== AFTER SWAP ===")
        print(f"New Counterparty Staff Shift ID: {counterparty_staff_shift_id_swaped}")
        print(f"New Initiator Staff Shift ID: {initiator_staff_shift_id_swaped}")

        await StaffShift.objects.abulk_update([counterparty_staff_shift_id_swaped, initiator_staff_shift_id_swaped], ['shift_id'])

        # Create history record
        branch_id = swap_request.branch_id
        details = {
            "reason": _(f"Accepted swap on {swap_request.desired_date} with shift {counterparty_shift.name}"),
            "initiator": swap_request.initiator.username,
            "counterparty": swap_request.counterparty.username,
            "initiator_shift": initiator_shift.name,
            "counterparty_shift": counterparty_shift.name,
            "branch": branch_id
        }

        # Log activity
        log_activity.delay(request.user.id, 'shift_swap_accept', details, branch_id, 'branch')

        # Notify managers via WebSocket
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"{branch_id}_{request.user.role}",
            {
                'type': 'branch.update',
                'signal': 'shift_swap_completed',
                'message': {
                    'swap_id': swap_request.id,
                    'details': details,
                }
            }
        )
        return Response({"status": _("Shift swap successful")}, status=status.HTTP_200_OK)
    
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
    
    async def create(self, request, *args, **kwargs):
        """Create a shift with Redis caching."""
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch'] = int(request.data['branches'][0])
        serializer = self.get_serializer(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        shift_pattern = ShiftPattern(**serializer.validated_data)
        await shift_pattern.asave()
        # Invalidate branch shifts cache

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="regenerate"
    )
    async def regenerate(self, request, pk=None):
        """
        Endpoint: POST /api/shift-patterns/1/regenerate/
        Request: {} (empty body)k
        """
        pattern_id = int(request.pattern_id)
        await ShiftUpdateHandler.handle_pattern_change(pattern_id)
        return Response({"status": _("Shift regeneration queued")}, status=status.HTTP_202_ACCEPTED)

from notifications.tasks import send_batch_notifications
class OvertimeRequestViewSet(ModelViewSet):
    """API for overtime requests."""
    queryset = OvertimeRequest.objects.all()
    serializer_class = OvertimeRequestSerializer

    def get_permissions(self):
        role_value = async_to_sync(self.request.user.get_role_value)()
        self._access_policy = (ScopeAccessPolicy if role_value <= 4 else StaffAccessPolicy)()
        return [self._access_policy, OvertimeRequestPermission()]

    def get_queryset(self):
        user = self.request.user
        scope_filter = async_to_sync(self._access_policy.get_queryset_scope)(user, view=self)
        return self.queryset.filter(scope_filter)

    async def perform_create(self, serializer):
        """User creates an overtime request directly in view."""
        try:
            staff_shift = await StaffShift.objects.select_related('branch').aget(
                user=self.request.user,
                date=timezone.now().date()
            )
            ot_request = await sync_to_async(lambda: OvertimeRequest.objects.create(
                staff_shift=staff_shift,
                **serializer.validated_data
            ))()

            # Trigger notification task
            extra_context = {
                'date': staff_shift.date.strftime('%Y-%m-%d'),
                'ot_request_id': ot_request.id
            }
            send_batch_notifications.delay(
                restaurant_id = staff_shift.branch.restaurant_id,
                branch_id=staff_shift.branch_id,
                # country_id=country_id,
                message = _(f"Overtime has been requested by {self.request.user.username}"),
                subject = _("New Overtime Request"),
                extra_context=extra_context,
                template_name = "emails/overtime_request.html"
            )

            logger.info(f"OvertimeRequest created for user {self.request.user.id}, staff_shift {staff_shift.id}, id {ot_request.id}")
            serializer.instance = ot_request
        except StaffShift.DoesNotExist:
            logger.error(f"No StaffShift found for user {self.request.user.id} on {timezone.now().date()}")
            raise serializers.ValidationError("No shift assigned for today.")
        # except Exception as e:
        #     logger.error(f"Failed to create OvertimeRequest: {str(e)}")
        #     raise serializers.ValidationError(f"Failed to create request: {str(e)}")

    async def create(self, request, *args, **kwargs):
        """Override create to await async perform_create."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        await self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['post'], url_path='approve')
    async def approve_overtime(self, request, *args, **kwargs):
        """Manager approves an overtime request."""
        # if not request.user.is_staff:
        #     return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)
        pk = kwargs['pk']
        ot_request = await OvertimeRequest.objects.select_related('staff_shift__branch').aget(id=pk)
        await ot_request.approve()
        # Notify user via WebSocket
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"user_{ot_request.staff_shift.user_id}",
            {
                'model': 'overtime',
                'type': 'stakeholder.notification',
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