from rest_access_policy import AccessPolicy
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from CRE.models import Branch, Restaurant
from notifications.models import EmployeeTransfer

CustomUser = get_user_model()
class ScopeAccessPolicy(AccessPolicy):
    """
    Swift access policy to validate user scope based on group role.
    Ensures actions stay within role-specific boundaries.
    """
    statements = [
        {
            "principal": ["group:CompanyAdmin"],
            "action": ["*", "review"],
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
            "action": ["*", "review"],
            "effect": "allow",
            "condition": "is_within_scope",
            "requires": ["restaurants"],
        },
        {
            "principal": ["group:RestaurantManager"],
            "action": ["*", "review"],
            "effect": "allow",
            "condition": "is_within_scope",
            "requires": ["restaurants"],
        },
        {
            "principal": ["group:BranchManager"],
            "action": ["*"],
            "effect": "allow",
            "condition": "is_within_scope",
            "requires": ["branches"],
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
            "queryset_filter": lambda user, model: (
                {
                    Branch: Q(company__in=user.companies.all()),
                    Restaurant: Q(company__in=user.companies.all()),
                    EmployeeTransfer: Q(from_branch__restaurant__company__in=user.companies.all()) | 
                                        Q(from_restaurant__company__in=user.companies.all()),
                    CustomUser: Q(companies__in=user.companies.all())
                }.get(model, Q(pk__in=[])) # Default: no filter if model unrecognized
            ),
        },
        "CountryManager": {
            "scopes": lambda user: {
                'companies': set(user.companies.values_list('id', flat=True)),
                'countries': set(user.countries.values_list('id', flat=True)),
                'restaurants': set(Restaurant.objects.filter(country_id__in=user.countries.all()).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(country_id__in=user.countries.all()).values_list('id', flat=True)),
            },
            "queryset_filter": lambda user, model: (
                {
                    Branch: Q(country__in=user.countries.all()),
                    Restaurant: Q(country__in=user.countries.all()),
                    EmployeeTransfer: Q(from_branch__restaurant__country__in=user.countries.all()) | 
                                        Q(from_restaurant__country__in=user.countries.all()) | 
                                        Q(to_branch__restaurant__country__in=user.countries.all()) | 
                                        Q(to_restaurant__country__in=user.countries.all()),
                    CustomUser: Q(countries__in=user.countries.all())
                }.get(model, Q(pk__in=[]))
            ),
        },
        "RestaurantOwner": {
            "scopes": lambda user: {
                'restaurants': set(Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(
                    restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user))
                ).values_list('id', flat=True)),
            },
            "queryset_filter": lambda user, model: (
                {
                    Branch: Q(restaurant__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user))),
                    Restaurant: Q(id__in=user.restaurants.all()) | Q(created_by=user),
                    EmployeeTransfer: Q(from_branch__restaurant__in=user.restaurants.all()) | Q(initiated_by=user),
                    CustomUser: Q(restaurants__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)))
                }.get(model, Q(pk__in=[]))
            ),
        },
        "RestaurantManager": {
            "scopes": lambda user: {
                'restaurants': set(Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user)).values_list('id', flat=True)),
                'branches': set(Branch.objects.filter(
                    restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()))
                ).values_list('id', flat=True)),
            },
            'queryset_filter': lambda user, model: (
                {
                    Branch: Q(restaurant__in=user.restaurants.all()),
                    Restaurant: Q(id__in=user.restaurants.all()),
                    EmployeeTransfer: Q(from_branch__restaurant__in=user.restaurants.all()) | 
                                    Q(from_restaurant__in=user.restaurants.all()) 
                }.get(model, Q(pk__in=[]))
            )
        },
        "BranchManager": {
            "scopes": lambda user: {
                'branches': set(user.branches.values_list('id', flat=True)),
            },
            "queryset_filter": lambda user, model: (
                {
                    Branch: Q(id__in=user.branches.all()),
                    Restaurant: Q(branches__in=user.branches.all()),
                    EmployeeTransfer: Q(from_branch__in=user.branches.all()) | Q(manager=user)
                }.get(model, Q(pk__in=[]))
            ),
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

    async def get_queryset_scope(self, user, view=None):
        """Returns Q filter for queryset scoping."""
        config = await sync_to_async(self.get_role_config)(user)
        model = view.queryset.model
        filter_func = config.get("queryset_filter", lambda u, m: Q(pk__in=[]))
        return filter_func(user, model)


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
            "action": ["list", "partial_update", "branches", "employees"],
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
        },
        # Restaurant Manager: Limited access to their branches, including creation if they belong to a company
        {
            "action": ["list", "retrieve", "create", "update", "partial_update"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
        },
        # Country Manager: Can view all branches in their country
        {
            "action": ["list", "retrieve", "create", "update"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
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
        },
    ]

    
