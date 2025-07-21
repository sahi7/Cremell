from rest_access_policy import AccessPolicy
from django.db.models import Q
from asgiref.sync import sync_to_async, async_to_sync
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from CRE.models import *
from zMisc.utils import HighRoleQsFilter, get_scopes_and_groups
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

    # SCOPE_CONFIG = {
    #     # TOD0: Filter status=active or is_active=true for companies, restaurants and branches checks 
    #     "CompanyAdmin": {
    #         "scopes": lambda user: {
    #             'companies': set(user.companies.values_list('id', flat=True)),
    #             'countries': set(user.countries.values_list('id', flat=True)),
    #             'restaurants': set(Restaurant.objects.filter(company_id__in=user.companies.all()).values_list('id', flat=True)),
    #             'branches': set(Branch.objects.filter(company_id__in=user.companies.all()).values_list('id', flat=True)),
    #         },
    #         "queryset_filter": lambda user, model: (
    #             {
    #                 OrderItem: Q(order__branch__company__in=user.companies.all()),
    #                 Order: Q(branch__company__in=user.companies.all()),
    #                 MenuItem: Q(category__menu__branch__company__in=user.companies.all()),
    #                 MenuCategory: Q(menu__branch__company__in=user.companies.all()),
    #                 Menu: Q(branch__company__in=user.companies.all()),
    #                 OvertimeRequest: Q(staff_shift__branch__company__in=user.companies.all()),
    #                 StaffShift: Q(branch__company__in=user.companies.all()),
    #                 ShiftPattern: Q(branch__company__in=user.companies.all()),
    #                 Shift: Q(branch__restaurant__company__in=user.companies.all()),
    #                 Branch: Q(company__in=user.companies.all()),
    #                 Restaurant: Q(company__in=user.companies.all()),
    #                 EmployeeTransfer: Q(from_branch__restaurant__company__in=user.companies.all()) | 
    #                                     Q(from_restaurant__company__in=user.companies.all()),
    #                 CustomUser: Q(companies__in=user.companies.all()),
    #             }.get(model, Q(pk__in=[])) # Default: no filter if model unrecognized
    #         ),
    #     },
    #     "CountryManager": {
    #         "scopes": lambda user: {
    #             'companies': set(user.companies.values_list('id', flat=True)),
    #             'countries': set(user.countries.values_list('id', flat=True)),
    #             'restaurants': set(Restaurant.objects.filter(country_id__in=user.countries.all()).values_list('id', flat=True)),
    #             'branches': set(Branch.objects.filter(country_id__in=user.countries.all()).values_list('id', flat=True)),
    #         },
    #         "queryset_filter": lambda user, model: (
    #             {
    #                 OrderItem: Q(order__branch__company__in=user.companies.all()) & Q(order__branch__country__in=user.countries.all()),
    #                 Order: Q(branch__company__in=user.companies.all()) & Q(branch__country__in=user.countries.all()),
    #                 MenuItem: Q(category__menu__branch__company__in=user.companies.all()) & Q(category__menu__branch__country__in=user.countries.all()),
    #                 MenuCategory: Q(menu__branch__company__in=user.companies.all()) & Q(menu__branch__country__in=user.countries.all()),
    #                 Menu: Q(branch__company__in=user.companies.all()) & Q(branch__country__in=user.countries.all()),
    #                 OvertimeRequest: Q(staff_shift__branch__company__in=user.companies.all()) & Q(staff_shift__branch__country__in=user.countries.all()),
    #                 StaffShift: Q(branch__company__in=user.companies.all()) & Q(branch__country__in=user.countries.all()),
    #                 ShiftPattern: Q(branch__company__in=user.companies.all()) & Q(branch__country__in=user.countries.all()) ,
    #                 Shift: Q(branch__restaurant__company__in=user.companies.all()) & Q(branch__restaurant__country__in=user.countries.all()) ,
    #                 Branch: Q(company_id__in=user.companies.all())  & Q(country_id__in=user.countries.all()),
    #                 Restaurant: Q(company_id__in=user.companies.all()) & Q(country_id__in=user.countries.all()),
    #                 EmployeeTransfer: Q(from_branch__restaurant__country__in=user.countries.all()) | 
    #                                     Q(from_restaurant__country__in=user.countries.all()) | 
    #                                     Q(to_branch__restaurant__country__in=user.countries.all()) | 
    #                                     Q(to_restaurant__country__in=user.countries.all()),
    #                 CustomUser: Q(countries__in=user.countries.all())
    #             }.get(model, Q(pk__in=[]))
    #         ),
    #     },
    #     "RestaurantOwner": {
    #         "scopes": lambda user: {
    #             'restaurants': set(Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)).values_list('id', flat=True)),
    #             'branches': set(Branch.objects.filter(
    #                 restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user))
    #             ).values_list('id', flat=True)),
    #         },
    #         "queryset_filter": lambda user, model: (
    #             {
    #                 OrderItem: Q(order__branch__restaurant__in=user.restaurants.all()),
    #                 Order: Q(branch__restaurant__in=user.restaurants.all()),
    #                 MenuItem: Q(category__menu__branch__restaurant__in=user.restaurants.all()),
    #                 MenuCategory: Q(menu__branch__restaurant__in=user.restaurants.all()),
    #                 Menu: Q(branch__restaurant__in=user.restaurants.all()),
    #                 OvertimeRequest: Q(staff_shift__branch__restaurant__in=user.restaurants.all()),
    #                 StaffShift: Q(branch__restaurant__in=user.restaurants.all()),
    #                 ShiftPattern: Q(branch__restaurant__in=user.restaurants.all()),
    #                 Shift: Q(branch__restaurant__in=user.restaurants.all()),
    #                 Branch: Q(restaurant__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user))),
    #                 Restaurant: Q(id__in=user.restaurants.all()) | Q(created_by=user),
    #                 EmployeeTransfer: Q(from_branch__restaurant__in=user.restaurants.all()) | Q(initiated_by=user),
    #                 CustomUser: Q(restaurants__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(created_by=user)))
    #             }.get(model, Q(pk__in=[]))
    #         ),
    #     },
    #     "RestaurantManager": {
    #         "scopes": lambda user: {
    #             'restaurants': set(Restaurant.objects.filter(Q(id__in=user.restaurants.all()) | Q(manager=user)).values_list('id', flat=True)),
    #             'branches': set(Branch.objects.filter(
    #                 restaurant_id__in=Restaurant.objects.filter(Q(id__in=user.restaurants.all()))
    #             ).values_list('id', flat=True)),
    #         },
    #         'queryset_filter': lambda user, model: (
    #             {
    #                 OrderItem: Q(order__branch__restaurant__in=user.restaurants.all()),
    #                 Order: Q(branch__restaurant__in=user.restaurants.all()),
    #                 MenuItem: Q(category__menu__branch__restaurant__in=user.restaurants.all()),
    #                 MenuCategory: Q(menu__branch__restaurant_id__in=user.restaurants.all()),
    #                 Menu: Q(branch__restaurant__in=user.restaurants.all()),
    #                 OvertimeRequest: Q(staff_shift__branch__restaurant__in=user.restaurants.all()),
    #                 StaffShift: Q(branch__restaurant__in=user.restaurants.all()),
    #                 ShiftPattern: Q(branch__restaurant__in=user.restaurants.all()),
    #                 Shift: Q(branch__restaurant__in=user.restaurants.all()),
    #                 Branch: Q(restaurant__in=user.restaurants.all()),
    #                 Restaurant: Q(id__in=user.restaurants.all()),
    #                 EmployeeTransfer: Q(from_branch__restaurant__in=user.restaurants.all()) | 
    #                                 Q(from_restaurant__in=user.restaurants.all()) 
    #             }.get(model, Q(pk__in=[]))
    #         )
    #     },
    #     "BranchManager": {
    #         "scopes": lambda user: {
    #             'branches': set(user.branches.values_list('id', flat=True)),
    #         },
    #         "queryset_filter": lambda user, model: (
    #             {
    #                 OrderItem: Q(order__branch_id__in=user.branches.all()),
    #                 Order: Q(branch_id__in=user.branches.all()),
    #                 MenuItem: Q(category__menu__branch__in=user.branches.all()),
    #                 MenuCategory: Q(menu__branch_id__in=user.branches.all()),
    #                 Menu: Q(branch_id__in=user.branches.all()),
    #                 OvertimeRequest: Q(staff_shift__branch_id__in=user.branches.all()),
    #                 StaffShift: Q(branch_id__in=user.branches.all()),
    #                 ShiftPattern: Q(branch_id__in=user.branches.all()),
    #                 Shift: Q(branch_id__in=user.branches.all()),
    #                 Branch: Q(id__in=user.branches.all()),
    #                 Restaurant: Q(branches__in=user.branches.all()),
    #                 EmployeeTransfer: Q(from_branch__in=user.branches.all()) | Q(manager=user)
    #             }.get(model, Q(pk__in=[]))
    #         ),
    #     },
    # }

    SCOPE_CONFIG = {
        "CompanyAdmin": {
            "scopes": HighRoleQsFilter.ca_scopes,
            "queryset_filter": HighRoleQsFilter.ca_queryset_filter
        },
        "CountryManager": {
            "scopes": HighRoleQsFilter.cm_scopes,
            "queryset_filter": HighRoleQsFilter.cm_queryset_filter
        },
        "RestaurantOwner": {
            "scopes": HighRoleQsFilter.ro_scopes,
            "queryset_filter": HighRoleQsFilter.ro_queryset_filter
        },
        "RestaurantManager": {
            "scopes": HighRoleQsFilter.rm_scopes,
            "queryset_filter": HighRoleQsFilter.rm_queryset_filter
        },
        "BranchManager": {
            "scopes": HighRoleQsFilter.bm_scopes,
            "queryset_filter": HighRoleQsFilter.bm_queryset_filter
        }
    }

    def get_role_config(self, user_groups):
        """Get config for the user's role."""
        group = next((g for g in user_groups if g in self.SCOPE_CONFIG), None)
        return self.SCOPE_CONFIG.get(group, {}) if group else {}

    def is_within_scope(self, request, view, action):
        """Unified scope check for all roles."""
        user = request.user
        user_scopes = async_to_sync(get_scopes_and_groups)(user.id)
        config = self.get_role_config(user_scopes['groups'])
        print("config: ", config)
        if not config:
            return False

        allowed_scopes = async_to_sync(config["scopes"])(user)
        print("allowed_scopes: ", allowed_scopes)
        requested = {
            'companies': set(request.data.get("companies", [])),
            'countries': set(request.data.get("countries", [])),
            'restaurants': set(request.data.get("restaurants", [])),
            'branches': set(request.data.get("branches", [])),
        }
        SCOPE_MAPPING = {
            'companies': 'company',
            'countries': 'country',
            'restaurants': 'restaurant',
            'branches': 'branch'
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
                # scope_key = SCOPE_MAPPING.get(field, field)
                # allowed = allowed_scopes.get(scope_key, set())
                allowed = allowed_scopes.get(field, set())
                is_valid = requested_ids.issubset(allowed)
                
                print(f"Checking {field}: {requested_ids} ⊆ {allowed} -> {is_valid}")

                if not is_valid:
                    return False

        return True

    async def get_queryset_scope(self, user, view=None):
        """Returns Q filter for queryset scoping."""
        user_scopes = await get_scopes_and_groups(user.id)
        print("user_scopes: ", user_scopes)
        config = await sync_to_async(self.get_role_config)(user_scopes['groups'])
        model = view.queryset.model
        scopes = await config.get("scopes", HighRoleQsFilter.default_scopes)(user)
        print("scopes: ", scopes)
        filter_func = config.get("queryset_filter", HighRoleQsFilter.default_queryset_filter)
        return await filter_func(user, model, scopes)
        # filter_func = config.get("queryset_filter", lambda u, m: Q(pk__in=[]))
        # return filter_func(user, model)


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

    
