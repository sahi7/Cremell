from rest_access_policy import AccessPolicy
from rest_framework.exceptions import PermissionDenied
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _
from CRE.models import Branch, Restaurant

class UserAccessPolicy(AccessPolicy):
    statements = [
        # Allow CompanyAdmin full access (validate companies within their scope)
        {
            "action": "*",
            "principal": ["group:CompanyAdmin"],
            "effect": "allow",
            "condition": "is_within_company_scope"
        },
        # Restrict RestaurantOwner actions (validate restaurants)
        {
            "action": ["list", "retrieve", "create", "update", "delete"],
            "principal": ["group:RestaurantOwner"],
            "effect": "allow",
            "condition": "is_within_restaurant_scope"
        },
        # Restrict CountryManager actions (validate countries and companies)
        {
            "action": ["list", "retrieve", "create", "update", "delete"],
            "principal": ["group:CountryManager"],
            "effect": "allow"
        },
        # Restrict RestaurantManager to read-only (validate restaurants)
        {
            "action": ["list", "retrieve"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
            "condition": "is_within_restaurant_scope"
        },
        # BranchManager: Read-only, checks branches
        {
            "action": ["list", "retrieve"],
            "principal": ["group:BranchManager"],
            "effect": "allow",
            "condition": "is_within_branch_manager_scope"
        },
    ]

    async def is_within_restaurant_scope(self, request, view, action):
        if action != "create":
            return True
        user = request.user
        requested_restaurants = set(request.data.get('restaurants', []))
        requested_companies = set(request.data.get('companies', []))
        
        # Cache group checks
        is_owner = await sync_to_async(user.groups.filter(name="RestaurantOwner").exists)()
        is_manager = await sync_to_async(user.groups.filter(name="RestaurantManager").exists)() if not is_owner else False
        
        if requested_restaurants:
            if is_owner:
                user_restaurants = set(await sync_to_async(lambda: list(user.restaurants.values_list('id', flat=True)))())
            elif is_manager:
                user_restaurants = set(await sync_to_async(lambda: list(user.restaurants.filter(manager=user).values_list('id', flat=True)))())
            else:
                raise PermissionDenied(_("Invalid role for restaurant scope."))
            if not requested_restaurants.issubset(user_restaurants):
                raise PermissionDenied(_("All requested restaurants must be within your scope."))
        
        if requested_companies:
            user_companies = set(await sync_to_async(lambda: list(user.companies.values_list('id', flat=True)))())
            if not requested_companies.issubset(user_companies):
                raise PermissionDenied(_("All requested companies must be within your scope."))
        
        role_to_create = request.data.get('role')
        request.data['status'] = 'active' if role_to_create in ['company_admin', 'country_manager', 'restaurant_owner'] else ('active' if request.data.get('branches', []) else 'pending')
        return True

    async def is_within_branch_manager_scope(self, request, view, action):
        if action != "create":
            return True
        user = request.user
        requested_branches = set(request.data.get('branches', []))
        requested_companies = set(request.data.get('companies', []))
        if requested_branches:
            user_branches = set(await sync_to_async(lambda: list(user.managed_branches.values_list('id', flat=True)))())  # Assumes related_name='managed_branches'
            if not requested_branches.issubset(user_branches):
                raise PermissionDenied(_("All requested branches must be within your scope."))
        if requested_companies:
            user_companies = set(await sync_to_async(lambda: list(user.companies.values_list('id', flat=True)))())
            if not requested_companies.issubset(user_companies):
                raise PermissionDenied(_("All requested companies must be within your scope."))
        role_to_create = request.data.get('role')
        request.data['status'] = 'active' if role_to_create in ['company_admin', 'country_manager', 'restaurant_owner'] else ('active' if requested_branches else 'pending')
        return True

class RestaurantAccessPolicy(AccessPolicy):
    statements = [
        {
            "action": ["list", "retrieve", "branches", "employees", "company"],
            "principal": ["group:CompanyAdmin", "group:RestaurantOwner", "group:CountryManager"],
            "effect": "allow",
        },
        {
            "action": ["create"],
            "principal": ["group:CompanyAdmin", "group:CountryManager"],
            "effect": "allow",
        },
        {
            "action": ["update", "partial_update"],
            "principal": ["group:RestaurantOwner", "group:CompanyAdmin"],
            "effect": "allow",
        },
        {
            "action": ["partial_update"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
        },
        {
            "action": ["destroy"],
            "principal": ["group:CompanyAdmin", "group:RestaurantOwner"],
            "effect": "allow",
        },
    ]


class BranchAccessPolicy(AccessPolicy):
    statements = [
        # Restaurant Owner: Full access to manage their restaurant's branches, including creation
        {
            "action": ["list", "retrieve", "create", "update", "partial_update", "destroy", "menus", "menu_detail", "employees"],
            "principal": ["group:RestaurantOwner"],
            "effect": "allow",
            "condition": "is_owner_of_restaurant",
        },
        # Restaurant Manager: Limited access to their branches, including creation if they belong to a company
        {
            "action": ["list", "retrieve", "create", "update", "partial_update"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
            "condition": "is_manager_of_restaurant",
        },
        # Country Manager: Can view all branches in their country
        {
            "action": ["list", "retrieve", "create", "update"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
            "condition": "is_in_country_manager_country",
        },
        # Company Admin: Full access to all branches
        {
            "action": ["list", "retrieve", "create", "update", "partial_update", "destroy"],
            "principal": ["group:CompanyAdmin"],
            "effect": "allow",
        },
        # Branch Manager: Can manage only the branch they are assigned to
        {
            "action": ["list", "retrieve", "update", "partial_update", "employees", "menus", "menu_detail"],
            "principal": ["group:BranchManager"],
            "effect": "allow",
            "condition": "is_manager_of_branch",
        },
    ]

    def is_owner_of_restaurant(self, request, view, action):
        """
        Check if the branch is linked to a restaurant created by the current user (for create, update, delete).
        Also, check if the restaurant is active before allowing branch creation.
        """
        print(f'Action: {action}') 
        if action in ["create", "update", "partial_update", "destroy"]:
            # For creating a branch, check if the restaurant belongs to the current user.
            restaurant = view.request.data.get('restaurant')
            if restaurant:
                return Restaurant.objects.filter(id=restaurant, created_by=request.user, status='active').exists()
        elif action in ["list", "retrieve"]:
            return True
        return False  # For non-create actions, allow if already satisfied

    def is_manager_of_restaurant(self, request, view, action):
        """
        Check if the branch belongs to a restaurant managed by the current user (for update, delete).
        Also, allow branch creation if the user belongs to a company.
        """
        if action in ["update", "partial_update", "destroy"]:
            restaurant = view.request.data.get('restaurant')
            if restaurant:
                return Restaurant.objects.filter(id=restaurant, manager=request.user).exists()
        elif action in ["list", "retrieve"]:
            print('yesfff')
            restaurant_id = view.kwargs.get('pk')
            if restaurant_id:
                return Restaurant.objects.filter(id=restaurant_id, created_by=request.user).exists()
        elif action == "create":
            # For branch creation, check if the user belongs to a company.
            return request.user.companies.exists()
        return False

    def is_in_country_manager_country(self, request, view, action):
        """
        Check if the branch is located within the country managed by the current user.
        """
        if action in ["list", "retrieve"]:
            restaurant = view.request.data.get('restaurant')
            if restaurant:
                return Restaurant.objects.filter(id=restaurant, country__in=request.user.countries.all()).exists()
        return False

    def is_manager_of_branch(self, request, view, action):
        """
        Check if the branch is managed by the current user (BranchManager).
        BranchManagers can only view and manage the branch they are assigned to.
        """
        if action in ["list", "retrieve", "update", "partial_update"]:
            # For list/retrieve, ensure the user can only view their branch.
            branch_id = view.kwargs.get('pk')  # Assuming the branch ID is passed in the URL
            if branch_id:
                return Branch.objects.filter(id=branch_id, manager=request.user).exists()
        return False


