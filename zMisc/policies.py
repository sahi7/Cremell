from rest_access_policy import AccessPolicy
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _
from CRE.models import Branch, Restaurant

class ScopeAccessPolicy(AccessPolicy):
    """
    Swift access policy to validate user scope based on group role.
    Ensures actions stay within role-specific boundaries.
    """
    statements = [
        {
            "principal": ["group:CompanyAdmin"],
            "action": ["*"],
            "effect": "allow",
            "condition": "is_within_scope",
            "requires": ["companies"],
        },
        {
            "principal": ["group:CountryManager"],
            "action": ["*"],
            "effect": "allow",
            "condition": "is_within_scope",
            "requires": ["countries", "companies"],
        },
        {
            "principal": ["group:RestaurantOwner"],
            "action": ["*"],
            "effect": "allow",
            "condition": "is_within_scope",
        },
        {
            "principal": ["group:RestaurantManager"],
            "action": ["*"],
            "effect": "allow",
            "condition": "is_within_scope",
        },
        {
            "principal": ["group:BranchManager"],
            "action": ["*"],
            "effect": "allow",
            "condition": "is_within_scope",
        },

    ]

    SCOPE_CONFIG = {
        "CompanyAdmin": {
            "scopes": lambda user: {
                'companies': set(user.companies.values_list('id', flat=True)),
                'countries': set(user.countries.values_list('id', flat=True)),
                'restaurants': set(Restaurant.objects.filter(company_id__in=user.companies.all()).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(company_id__in=user.companies.all()).values_list('id', flat=True)),
            },
            "queryset_filter": lambda user: Q(company_id__in=user.companies.all()),
        },
        "CountryManager": {
            "scopes": lambda user: {
                'companies': set(user.companies.values_list('id', flat=True)),
                'countries': set(user.countries.values_list('id', flat=True)),
                'restaurants': set(Restaurant.objects.filter(country_id__in=user.countries.all()).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(country_id__in=user.countries.all()).values_list('id', flat=True)),
            },
            "queryset_filter": lambda user: Q(country_id__in=user.countries.all()) & Q(company_id__in=user.companies.all()),
        },
        "RestaurantOwner": {
            "scopes": lambda user: {
                'restaurants': set(Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(
                    restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user))
                ).values_list('id', flat=True)),
            },
            "queryset_filter": lambda user: Q(restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user))),
        },
        "RestaurantManager": {
            "scopes": lambda user: {
                'restaurants': set(Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user)).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(
                    restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user))
                ).values_list('id', flat=True)),
            },
            "queryset_filter": lambda user: Q(restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user))),
        },
        "BranchManager": {
            "scopes": lambda user: {
                'branches': set(user.branches.values_list('id', flat=True)),
            },
            "queryset_filter": lambda user: Q(id__in=user.branches.all()),
        },
    }

    def get_role_config(self, user):
        """Get config for the user's role."""
        role = next((g.name for g in user.groups.all() if g.name in self.SCOPE_CONFIG), None)
        return self.SCOPE_CONFIG.get(role, {}) if role else {}

    def is_within_scope(self, request, view, action):
        """Unified scope check for all roles."""
        user = request.user
        config = self.get_role_config(user)
        if not config:
            return False

        allowed_scopes = config["scopes"](user)
        requested = {
            'companies': set(request.data.get("companies", [])),
            'countries': set(request.data.get("countries", [])),
            'restaurants': set(request.data.get("restaurants", [])),
            'branches': set(request.data.get("branches", [])),
        }

        if request.method.lower() in ['post', 'put', 'patch']:
            # Check required fields from statement
            requires = next((s.get("requires", []) for s in self.statements if "group:" + user.groups.first().name in s["principal"]), [])

            for field in requires:
                if not requested.get(field, set()):
                    return False

            # Validate requested IDs
            for field, requested_ids in requested.items():
                if not requested_ids:
                    continue
                allowed = allowed_scopes.get(field, set())
                is_valid = requested_ids.issubset(allowed)
                
                # print(f"Checking {field}: {requested_ids} ⊆ {allowed} -> {is_valid}")

                if not is_valid:
                    return False

        return True

    def get_queryset_scope(self, request, view):
        """Returns Q filter for queryset scoping."""
        user = request.user
        config = self.get_role_config(user)
        return config.get("queryset_filter", lambda u: Q(pk__in=[]))(user)


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


