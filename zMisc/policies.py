from rest_access_policy import AccessPolicy
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _
from CRE.models import Branch, Restaurant

class ScopeAccessPolicy(AccessPolicy):
    """
    Async access policy to validate user scope based on their role (group).
    Ensures actions (GET, CREATE, UPDATE) stay within role-specific boundaries.
    Use with adrf async Viewsets only - overide the views' dispatch method if not
    """
    statements = [
        {
            "principal": ["group:CompanyAdmin"],
            "action": ["*"],  # Applies to list, retrieve, create, etc.
            "condition": "is_within_company_scope",
        },
        {
            "principal": ["group:CountryManager"],
            "action": ["*"],
            "condition": "is_within_country_scope",
        },
        {
            "principal": ["group:RestaurantOwner"],
            "action": ["*"],
            "condition": "is_within_restaurant_owner_scope",
        },
        {
            "principal": ["group:RestaurantManager"],
            "action": ["*"],
            "condition": "is_within_restaurant_manager_scope",
        },
        {
            "principal": ["group:BranchManager"],
            "action": ["*"],
            "condition": "is_within_branch_scope",
        },
        # Deny all other roles by default
        {
            "principal": ["*"],
            "action": ["*"],
            "effect": "deny",
        },
    ]

    async def get_allowed_scopes(self, request, view, action):
        """Compute allowed entity IDs for the user's role asynchronously."""
        user = request.user
        # Get role from groups asynchronously
        role = await sync_to_async(lambda: next(
            (g.name for g in user.groups.all() if g.name in self.statements_by_principal), None
        ))()
        if not role:
            return {}

        if role == "CompanyAdmin":
            companies = await sync_to_async(lambda: set(user.companies.values_list('id', flat=True)))()
            return {
                'companies': companies,
                'countries': await sync_to_async(lambda: set(user.countries.values_list('id', flat=True)))(),
                'restaurants': await sync_to_async(lambda: set(
                    Restaurant.objects.filter(company_id__in=companies).values_list('id', flat=True)
                ))(),
                'branches': await sync_to_async(lambda: set(
                    Branch.objects.filter(company_id__in=companies).values_list('id', flat=True)
                ))(),
            }
        elif role == "CountryManager":
            countries = await sync_to_async(lambda: set(user.countries.values_list('id', flat=True)))()
            companies = await sync_to_async(lambda: set(user.companies.values_list('id', flat=True)))()
            return {
                'companies': companies,
                'countries': countries,
                'restaurants': await sync_to_async(lambda: set(
                    Restaurant.objects.filter(country_id__in=countries).values_list('id', flat=True)
                ))(),
                'branches': await sync_to_async(lambda: set(
                    Branch.objects.filter(country_id__in=countries).values_list('id', flat=True)
                ))(),
            }
        elif role == "RestaurantOwner":
            restaurants = await sync_to_async(lambda: set(
                Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)).values_list('id', flat=True)
            ))()
            return {
                'restaurants': restaurants,
                'branches': await sync_to_async(lambda: set(
                    Branch.objects.filter(restaurant_id__in=restaurants).values_list('id', flat=True)
                ))(),
            }
        elif role == "RestaurantManager":
            restaurants = await sync_to_async(lambda: set(
                Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user)).values_list('id', flat=True)
            ))()
            return {
                'restaurants': restaurants,
                'branches': await sync_to_async(lambda: set(
                    Branch.objects.filter(restaurant_id__in=restaurants).values_list('id', flat=True)
                ))(),
            }
        elif role == "BranchManager":
            return {
                'branches': await sync_to_async(lambda: set(user.branches.values_list('id', flat=True)))(),
            }
        return {}

    async def is_within_company_scope(self, request, view, action):
        return await self._check_scope(request, view, action, requires=['companies'])

    async def is_within_country_scope(self, request, view, action):
        return await self._check_scope(request, view, action)

    async def is_within_restaurant_owner_scope(self, request, view, action):
        return await self._check_scope(request, view, action)

    async def is_within_restaurant_manager_scope(self, request, view, action):
        return await self._check_scope(request, view, action)

    async def is_within_branch_scope(self, request, view, action):
        return await self._check_scope(request, view, action)

    async def _check_scope(self, request, view, action, requires=None):
        """Core async scope validation logic."""
        allowed_scopes = await self.get_allowed_scopes(request, view, action)
        requested = {
            'companies': set(request.data.get("companies", [])),
            'countries': set(request.data.get("countries", [])),
            'restaurants': set(request.data.get("restaurants", [])),
            'branches': set(request.data.get("branches", [])),
        }

        if action in ['create', 'update', 'partial_update']:
            if requires:
                for field in requires:
                    if not requested[field]:
                        return False  # Missing required field
            for field, requested_ids in requested.items():
                if requested_ids:
                    allowed_ids = allowed_scopes.get(field, set())
                    if not requested_ids.issubset(allowed_ids):
                        return False
        return True

    async def get_queryset_scope(self, request, view):
        """
        Returns a Q filter for queryset scoping asynchronously.
        Usage:
            scope_filter = await ScopeAccessPolicy().get_queryset_scope(self.request, self)
            return Branch.objects.filter(scope_filter) - for a Branch object
        """
        user = request.user
        role = await sync_to_async(lambda: next(
            (g.name for g in user.groups.all() if g.name in self.statements_by_principal), None
        ))()
        if not role:
            return Q(pk__in=[])  # Empty queryset

        if role == "CompanyAdmin":
            companies = await sync_to_async(lambda: list(user.companies.values_list('id', flat=True)))()
            return Q(company_id__in=companies)
        elif role == "CountryManager":
            countries = await sync_to_async(lambda: list(user.countries.values_list('id', flat=True)))()
            companies = await sync_to_async(lambda: list(user.companies.values_list('id', flat=True)))()
            return Q(country_id__in=countries) & Q(company_id__in=companies)
        elif role == "RestaurantOwner":
            restaurants = await sync_to_async(lambda: list(
                Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)).values_list('id', flat=True)
            ))()
            return Q(restaurant_id__in=restaurants)
        elif role == "RestaurantManager":
            restaurants = await sync_to_async(lambda: list(
                Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user)).values_list('id', flat=True)
            ))()
            return Q(restaurant_id__in=restaurants)
        elif role == "BranchManager":
            branches = await sync_to_async(lambda: list(user.branches.values_list('id', flat=True)))()
            return Q(id__in=branches)
        return Q(pk__in=[])  # Default deny

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


